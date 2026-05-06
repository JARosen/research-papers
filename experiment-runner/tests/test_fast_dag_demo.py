from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from experiment_runner.fast_dag_demo import _contamination_rate, _fast_demo_truth, run_fast_dag_demo
from experiment_runner.harnesses.base import HarnessRunResult
from experiment_runner.tasks import load_task


def _run_payload(final_output: str, *, artifact_hashes: dict[str, str] | None = None, intermediate_outputs: list[dict] | None = None) -> dict:
    return {
        "final_output": final_output,
        "duration_ms": 1,
        "model_usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "model_calls": 1,
        },
        "execution_metadata": {},
        "prompt_response_log": [],
        "intermediate_outputs": intermediate_outputs or [],
        "artifact_hashes": artifact_hashes or {},
    }


class FakeLoopRunner:
    def __init__(self, config) -> None:
        self.config = config

    def run_fresh(self, task, *, repeats=1, updated=False):
        final_output = "prior memo"
        return HarnessRunResult(
            payload={
                "runs": [
                    _run_payload(final_output)
                ],
                "final_output": final_output,
            }
        )

    def run_update(self, task, prior_run, edit, *, include_intermediates=False, use_procedural_memory=False, include_edit_event=False):
        if task.task_id == "unrelated_branch_noop_update":
            final_output = "prior memo"
        else:
            final_output = "Updated memo with budget-neutral year-one utilization controls for chronic-care follow-up."
        return HarnessRunResult(payload=_run_payload(final_output))


class FakeDAGRunner:
    def __init__(self, config) -> None:
        self.config = config

    def _run_graph(self, task, *, updated, allow_replay):
        if task.task_id == "unrelated_branch_noop_update":
            hashes = {
                "utilization_context": "a",
                "reimbursement_context": "b",
                "operations_context": "c",
                "access_cost_context": "d",
                "provider_recruiting_note": "old-note" if not updated else "new-note",
                "claim_matrix": "e",
                "tension_analysis": "f",
                "recommendation_criteria": "g",
                "final_memo": "memo",
            }
            final_output = "prior memo"
            outputs = [{"name": key, "content": key, "identity": key, "hash": value} for key, value in hashes.items()]
            return _run_payload(final_output, artifact_hashes=hashes, intermediate_outputs=outputs)
        hashes = {
            "utilization_context": "a",
            "reimbursement_context": "b",
            "operations_context": "c",
            "access_cost_context": "d",
            "claim_matrix": "e",
            "tension_analysis": "f",
            "recommendation_criteria": "old-criteria",
            "implementation_plan": "old-plan",
            "final_memo": "old-memo",
        }
        outputs = [{"name": key, "content": key, "identity": key, "hash": value} for key, value in hashes.items()]
        return _run_payload("prior memo", artifact_hashes=hashes, intermediate_outputs=outputs)

    def _run_graph_with_overrides(self, task, *, updated, allow_replay, stage_output_overrides=None):
        if task.task_id == "unrelated_branch_noop_update":
            return self._run_graph(task, updated=True, allow_replay=allow_replay)
        hashes = {
            "utilization_context": "a",
            "reimbursement_context": "b",
            "operations_context": "c",
            "access_cost_context": "d",
            "claim_matrix": "e",
            "tension_analysis": "f",
            "recommendation_criteria": "new-criteria",
            "implementation_plan": "new-plan",
            "final_memo": "new-memo",
        }
        outputs = [
            {"name": "recommendation_criteria", "content": "budget-neutral utilization controls year-one chronic-care", "identity": "rc", "hash": "new-criteria"},
            {"name": "implementation_plan", "content": "budget-neutral implementation with utilization controls", "identity": "ip", "hash": "new-plan"},
            {"name": "final_memo", "content": "Updated memo with budget-neutral year-one utilization controls for chronic-care follow-up.", "identity": "fm", "hash": "new-memo"},
        ]
        return _run_payload(
            "Updated memo with budget-neutral year-one utilization controls for chronic-care follow-up.",
            artifact_hashes=hashes,
            intermediate_outputs=outputs,
        )


class FastDAGDemoTests(unittest.TestCase):
    def _noop_truth(self) -> dict:
        task = load_task(
            Path("/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiments/execution_lineage/tasks/unrelated_branch_noop_update/task.json")
        )
        return _fast_demo_truth(task)

    def test_noop_contamination_ignores_clean_preserved_memo_with_unrelated_artifacts_elsewhere(self) -> None:
        truth = self._noop_truth()
        final_memo = (
            "Recommendation: Preserve the prior telehealth recommendation exactly. "
            "Note on decision-irrelevant request: No recruiting/staffing update is included because no recruiting sources are present "
            "in the declared artifacts and such content is explicitly excluded as decision-irrelevant in the current context. "
            "Open Questions: The operational feasibility and rollout pace impact should be tested early."
        )
        result_bundle = {
            "intermediate_outputs": [
                {"name": "provider_recruiting_note", "content": "R1_current virtual-first roles and workforce planning."}
            ],
            "prompt_response_log": [
                {"prompt": "A provider recruiting and staffing analysis was updated."}
            ],
        }
        self.assertEqual(_contamination_rate(final_memo, truth), 0.0)
        self.assertIn("provider_recruiting_note", str(result_bundle))

    def test_noop_contamination_allows_clean_rewrite(self) -> None:
        truth = self._noop_truth()
        final_memo = (
            "Recommendation: Continue the same selective telehealth expansion. "
            "Current Evidence: Reimbursement remains selective, compliance remains state-clustered, and cost controls remain necessary. "
            "Operational Implications: Build compliance staffing and training capacity in tighter state clusters."
        )
        self.assertEqual(_contamination_rate(final_memo, truth), 0.0)

    def test_noop_contamination_flags_recruiting_branch_import(self) -> None:
        truth = self._noop_truth()
        final_memo = (
            "Recommendation: Expand more aggressively because provider recruiting has improved. "
            "The updated provider staffing analysis shows virtual-first roles improve recruiting reach and coverage flexibility, "
            "so workforce planning now supports broader telehealth expansion."
        )
        self.assertEqual(_contamination_rate(final_memo, truth), 1.0)

    def test_noop_contamination_flags_recruiting_source_citation(self) -> None:
        truth = self._noop_truth()
        final_memo = (
            "Current Evidence: The updated recruiting note supports expansion and should affect the rollout strategy (R1_current)."
        )
        self.assertEqual(_contamination_rate(final_memo, truth), 1.0)

    def test_run_fast_dag_demo_writes_expected_bundle(self) -> None:
        config = SimpleNamespace(
            model_provider="openai",
            model_name="gpt-test",
            model_temperature=0.0,
            model_seed=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            with patch("experiment_runner.fast_dag_demo.LoopCentricHarnessRunner", FakeLoopRunner), patch(
                "experiment_runner.fast_dag_demo.SimpleDAGHarnessRunner", FakeDAGRunner
            ):
                summary = run_fast_dag_demo(config=config, output_dir=output_dir, repeats=1, judge_repeats=1)
            self.assertEqual(summary["row_count"], 6)
            self.assertTrue((output_dir / "fast_demo_results.json").exists())
            self.assertTrue((output_dir / "summary_by_task_condition.csv").exists())
            rows = json.loads((output_dir / "rows_by_task_condition_repeat.json").read_text())
            self.assertEqual(len(rows), 6)
            noop_dag = next(row for row in rows if row["task_id"] == "unrelated_branch_noop_update" and row["condition_id"] == "simple_dag_replay_selective_recompute")
            self.assertEqual(noop_dag["final_output_exact_match"], True)
            self.assertEqual(noop_dag["stable_artifact_hash_preservation"], 1.0)
            self.assertEqual(noop_dag["unrelated_branch_contamination_rate"], 0.0)
            self.assertEqual(noop_dag["output_faithfulness_score"], 1.0)
            self.assertEqual(noop_dag["current_state_precision_score"], 1.0)
            intermediate_dag = next(row for row in rows if row["task_id"] == "intermediate_artifact_edit" and row["condition_id"] == "simple_dag_replay_selective_recompute")
            self.assertEqual(intermediate_dag["downstream_propagation_recall"], 1.0)
            self.assertGreaterEqual(intermediate_dag["final_memo_constraint_reflection"], 0.75)


if __name__ == "__main__":
    unittest.main()
