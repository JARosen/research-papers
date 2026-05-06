from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from experiment_runner.harnesses.loop_centric.runner import LoopCentricHarnessRunner
from experiment_runner.harnesses.simple_dag.runner import SimpleDAGHarnessRunner
from experiment_runner.tasks import load_task


TASK_FILE = Path("experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1/task.json")
MULTI_EDIT_TASK_FILE = Path("experiments/execution_lineage/tasks/multi_edit_interaction_update/task.json")
NOOP_TASK_FILE = Path("experiments/execution_lineage/tasks/unrelated_branch_noop_update/task.json")
INTERMEDIATE_EDIT_TASK_FILE = Path("experiments/execution_lineage/tasks/intermediate_artifact_edit/task.json")


def assistant_output_item(text: str, *, item_id: str) -> dict:
    return {
        "id": item_id,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [
            {
                "type": "output_text",
                "text": text,
                "annotations": [],
            }
        ],
    }


class QueueModelClient:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)

    def generate_items(self, **kwargs):
        if not self.responses:
            raise AssertionError(f"No fake responses remaining for {kwargs.get('step_name')}")
        response = dict(self.responses.pop(0))
        response.setdefault("step_name", kwargs.get("step_name"))
        response.setdefault("text", "")
        response.setdefault("input_tokens", 10)
        response.setdefault("output_tokens", 5)
        response.setdefault("duration_ms", 1)
        response.setdefault("response_id", f"resp_{len(self.responses)}")
        response.setdefault("output_items", [])
        return response


def make_loop_runner(responses: list[dict]) -> LoopCentricHarnessRunner:
    runner = LoopCentricHarnessRunner.__new__(LoopCentricHarnessRunner)
    runner.config = SimpleNamespace(
        model_name="gpt-test",
        model_provider="openai",
        loop_context_strategy="rolling_summary_plus_memory_tool",
        loop_summary_trigger_tokens=80_000,
        loop_summary_keep_messages=4,
    )
    runner.model_client = QueueModelClient(responses)
    return runner


def make_dag_runner(responses: list[dict]) -> SimpleDAGHarnessRunner:
    runner = SimpleDAGHarnessRunner.__new__(SimpleDAGHarnessRunner)
    runner.config = SimpleNamespace(
        model_name="gpt-test",
        model_provider="openai",
    )
    runner.model_client = QueueModelClient(responses)
    runner.cache = {}
    return runner


def six_step_outputs(prefix: str) -> list[dict]:
    return [
        {
            "text": f"{prefix}-{idx}",
            "output_items": [assistant_output_item(f"{prefix}-{idx}", item_id=f"{prefix}_{idx}")],
        }
        for idx in range(8)
    ]


def single_output(text: str, *, item_id: str) -> list[dict]:
    return [
        {
            "text": text,
            "output_items": [assistant_output_item(text, item_id=item_id)],
        }
    ]


class RunnerSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = load_task(TASK_FILE)

    def test_loop_fresh_smoke(self) -> None:
        runner = make_loop_runner(six_step_outputs("fresh"))
        result = runner.run_fresh(self.task, repeats=1).payload
        self.assertEqual(result["final_output"], "fresh-7")

    def test_loop_update_final_only_smoke(self) -> None:
        runner = make_loop_runner(six_step_outputs("fresh") + single_output("update-final-only", item_id="update_final"))
        fresh = runner.run_fresh(self.task, repeats=1)
        update = runner.run_update(self.task, fresh, self.task.primary_edit).payload
        self.assertEqual(update["final_output"], "update-final-only")

    def test_loop_update_with_edit_event_smoke(self) -> None:
        runner = make_loop_runner(six_step_outputs("fresh") + single_output("update-edit-event", item_id="update_edit_event"))
        fresh = runner.run_fresh(self.task, repeats=1)
        update = runner.run_update(self.task, fresh, self.task.primary_edit, include_edit_event=True).payload
        self.assertEqual(update["final_output"], "update-edit-event")

    def test_loop_update_with_intermediates_smoke(self) -> None:
        runner = make_loop_runner(
            six_step_outputs("fresh") + single_output("update-with-intermediates", item_id="update_notes")
        )
        fresh = runner.run_fresh(self.task, repeats=1)
        update = runner.run_update(self.task, fresh, self.task.primary_edit, include_intermediates=True).payload
        self.assertEqual(update["final_output"], "update-with-intermediates")

    def test_loop_procedural_memory_smoke(self) -> None:
        responses = six_step_outputs("fresh-no-memory")
        responses += [
            {
                "text": "",
                "output_items": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "memory_list",
                        "arguments": "{}",
                    }
                ],
            },
            {
                "text": "",
                "output_items": [
                    {
                        "type": "function_call",
                        "call_id": "call_2",
                        "name": "memory_get",
                        "arguments": '{"id":"workflow_recipe_v1"}',
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_3",
                        "name": "memory_get",
                        "arguments": '{"id":"source_version_notes_v1"}',
                    },
                ],
            },
            {
                "text": "memory-update-0",
                "output_items": [assistant_output_item("memory-update-0", item_id="mu_0")],
            },
        ]
        responses += []
        runner = make_loop_runner(responses)

        fresh = runner.run_fresh(self.task, repeats=1)
        update = runner.run_update(
            self.task,
            fresh,
            self.task.primary_edit,
            include_intermediates=True,
            use_procedural_memory=True,
        ).payload
        self.assertEqual(update["final_output"], "memory-update-0")
        self.assertTrue(update["memory_metadata"]["memory_tool_calls"])

    def test_loop_real_world_staged_update_smoke(self) -> None:
        runner = make_loop_runner(six_step_outputs("fresh") + six_step_outputs("staged-update"))
        fresh = runner.run_fresh(self.task, repeats=1)
        update = runner.run_staged_update(self.task, fresh, self.task.primary_edit).payload
        self.assertEqual(update["final_output"], "staged-update-7")

    def test_simple_dag_smoke(self) -> None:
        update_responses = [
            {
                "text": "dag-update-reimbursement",
                "output_items": [assistant_output_item("dag-update-reimbursement", item_id="dur_0")],
            },
            {
                "text": "dag-update-claim-matrix",
                "output_items": [assistant_output_item("dag-update-claim-matrix", item_id="duc_1")],
            },
            {
                "text": "dag-update-recommendation-criteria",
                "output_items": [assistant_output_item("dag-update-recommendation-criteria", item_id="durc_2")],
            },
            {
                "text": "dag-update-final-memo",
                "output_items": [assistant_output_item("dag-update-final-memo", item_id="dufm_3")],
            },
        ]
        runner = make_dag_runner(six_step_outputs("dag-replay") + six_step_outputs("dag-fresh") + update_responses)
        result = runner.run_all(self.task, replay_repeats=1, fresh_repeats=1, include_update=True)
        self.assertEqual(result["simple_dag_fresh_recompute"].payload["final_output"], "dag-fresh-7")
        self.assertEqual(
            result["simple_dag_replay_selective_recompute"].payload["updated_run"]["final_output"],
            "dag-update-final-memo",
        )
        updated = result["simple_dag_replay_selective_recompute"].payload["updated_run"]
        self.assertEqual(updated["execution_metadata"]["cache_hits"], 4)
        self.assertEqual(updated["execution_metadata"]["cache_misses"], 4)
        self.assertEqual(
            updated["execution_metadata"]["execution_sources_by_step"]["utilization_context"]["execution_source"],
            "replay",
        )
        self.assertEqual(
            updated["execution_metadata"]["execution_sources_by_step"]["reimbursement_context"]["execution_source"],
            "fresh",
        )

    def test_simple_dag_non_source_stage_identity_ignores_full_source_hash(self) -> None:
        runner = make_dag_runner([])
        identity_initial = runner._stage_identity(
            task=self.task,
            stage_name="claim_matrix",
            prompt_name="simple_dag/claim_matrix.md",
            source_text=self.task.render_sources(updated=False),
            dependency_artifacts=[],
            source_backed=False,
        )
        identity_updated = runner._stage_identity(
            task=self.task,
            stage_name="claim_matrix",
            prompt_name="simple_dag/claim_matrix.md",
            source_text=self.task.render_sources(updated=True),
            dependency_artifacts=[],
            source_backed=False,
        )
        self.assertEqual(identity_initial, identity_updated)

    def test_multi_edit_task_stage_routing_smoke(self) -> None:
        task = load_task(MULTI_EDIT_TASK_FILE)
        self.assertIn("S3_current", task.render_sources_for_stage(updated=True, stage_name="operations_context"))
        self.assertIn("S4_current", task.render_sources_for_stage(updated=True, stage_name="access_cost_context"))
        self.assertIn("S5_current", task.render_sources_for_stage(updated=True, stage_name="access_cost_context"))

    def test_unrelated_branch_task_routes_recruiting_note_to_isolated_stage(self) -> None:
        task = load_task(NOOP_TASK_FILE)
        recruiting = task.render_sources_for_stage(updated=True, stage_name="provider_recruiting_note")
        self.assertIn("R1_current", recruiting)
        self.assertNotIn("S2_current", recruiting)

    def test_dag_stage_override_updates_only_downstream_identity(self) -> None:
        task = load_task(INTERMEDIATE_EDIT_TASK_FILE)
        stage_count = len(task.workflow_stages)
        responses = [
            {
                "text": f"initial-{idx}",
                "output_items": [assistant_output_item(f"initial-{idx}", item_id=f"initial_{idx}")],
            }
            for idx in range(stage_count)
        ]
        responses.extend(
            [
                {
                    "text": f"updated-{idx}",
                    "output_items": [assistant_output_item(f"updated-{idx}", item_id=f"updated_{idx}")],
                }
                for idx in range(2)
            ]
        )
        runner = make_dag_runner(responses)
        initial = runner._run_graph(task, updated=False, allow_replay=True)
        updated = runner._run_graph_with_overrides(
            task,
            updated=True,
            allow_replay=True,
            stage_output_overrides=task.updated_stage_overrides,
        )
        initial_hashes = initial["artifact_hashes"]
        updated_hashes = updated["artifact_hashes"]
        self.assertEqual(initial_hashes["utilization_context"], updated_hashes["utilization_context"])
        self.assertNotEqual(initial_hashes["recommendation_criteria"], updated_hashes["recommendation_criteria"])
        self.assertNotEqual(initial_hashes["implementation_plan"], updated_hashes["implementation_plan"])


if __name__ == "__main__":
    unittest.main()
