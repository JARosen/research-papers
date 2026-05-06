from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from experiment_runner.cli import _build_bundle, _write_rendered_prompts
from experiment_runner.config import RunnerConfig
from experiment_runner.harnesses.loop_centric import prompts as prompt_module
from experiment_runner.harnesses.loop_centric.runner import LoopCentricHarnessRunner
from experiment_runner.harnesses.simple_dag.runner import SimpleDAGHarnessRunner
from experiment_runner.tasks import load_task


TASK_FILE = Path("experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1/task.json")
MULTI_EDIT_TASK_FILE = Path("experiments/execution_lineage/tasks/multi_edit_interaction_update/task.json")
INTERMEDIATE_EDIT_TASK_FILE = Path("experiments/execution_lineage/tasks/intermediate_artifact_edit/task.json")

FORBIDDEN_LOOP_STRINGS = [
    "expected_affected_claim_ids",
    "expected_unaffected_claim_ids",
    "expected_recomputed_stages",
    "allowed_artifacts",
    "disallowed_context",
    "Old source id:",
    "New source id:",
    "should_affect_final_memo",
]


def make_loop_runner() -> LoopCentricHarnessRunner:
    runner = LoopCentricHarnessRunner.__new__(LoopCentricHarnessRunner)
    runner.config = SimpleNamespace(
        model_name="gpt-test",
        model_provider="openai",
        loop_context_strategy="rolling_summary_plus_memory_tool",
        loop_summary_trigger_tokens=80_000,
        loop_summary_keep_messages=4,
    )
    runner.model_client = None
    return runner


def make_dag_runner() -> SimpleDAGHarnessRunner:
    runner = SimpleDAGHarnessRunner.__new__(SimpleDAGHarnessRunner)
    runner.config = SimpleNamespace(model_name="gpt-test", model_provider="openai")
    runner.model_client = None
    runner.cache = {}
    return runner


class PromptIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = load_task(TASK_FILE)

    def test_changing_loop_real_world_prompt_does_not_change_dag_prompt(self) -> None:
        runner = make_dag_runner()
        original = runner._build_stage_prompt(
            task=self.task,
            stage_name="reimbursement_context",
            prompt_name="simple_dag/reimbursement_context.md",
            source_text=self.task.render_sources_for_stage(updated=True, stage_name="reimbursement_context"),
            dependency_artifacts=[],
            edits=(self.task.primary_edit,),
            stage=next(item for item in self.task.workflow_stages if item.id == "reimbursement_context"),
            source_backed=True,
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            shutil.copytree(prompt_module.PROMPTS_DIR, temp_root, dirs_exist_ok=True)
            (temp_root / "loop_real_world" / "final_update.md").write_text("COMPLETELY DIFFERENT LOOP PROMPT")
            with patch.object(prompt_module, "PROMPTS_DIR", temp_root):
                changed = runner._build_stage_prompt(
                    task=self.task,
                    stage_name="reimbursement_context",
                    prompt_name="simple_dag/reimbursement_context.md",
                    source_text=self.task.render_sources_for_stage(updated=True, stage_name="reimbursement_context"),
                    dependency_artifacts=[],
                    edits=(self.task.primary_edit,),
                    stage=next(item for item in self.task.workflow_stages if item.id == "reimbursement_context"),
                    source_backed=True,
                )

        self.assertEqual(original, changed)

    def test_simple_dag_update_prompt_includes_source_replacement_metadata(self) -> None:
        runner = make_dag_runner()
        prompt = runner._build_stage_prompt(
            task=self.task,
            stage_name="reimbursement_context",
            prompt_name="simple_dag/reimbursement_context.md",
            source_text=self.task.render_sources_for_stage(updated=True, stage_name="reimbursement_context"),
            dependency_artifacts=[],
            edits=(self.task.primary_edit,),
            stage=next(item for item in self.task.workflow_stages if item.id == "reimbursement_context"),
            source_backed=True,
        )
        self.assertIn(f"Old source id: {self.task.primary_edit.old_source_id}", prompt)
        self.assertIn(f"New source id: {self.task.primary_edit.new_source_id}", prompt)
        self.assertIn("Runtime execution metadata:", prompt)
        self.assertIn("Requested output format:", prompt)

    def test_loop_real_world_prompts_do_not_include_oracle_metadata(self) -> None:
        runner = make_loop_runner()
        prior_final = "Prior memo text"
        prior_intermediates = [{"name": "claim_matrix", "content": "Old notes"}]

        messages_and_instructions = [
            (
                prompt_module.load_prompt("loop_real_world/final_update.md"),
                runner._build_real_world_update_message(
                    task_bundle=self.task,
                    source_text=self.task.render_sources(updated=True),
                    prior_final_output=prior_final,
                    prior_intermediates=None,
                    edits=(),
                ),
            ),
            (
                prompt_module.load_prompt("loop_real_world/with_edit_event.md"),
                runner._build_real_world_update_message(
                    task_bundle=self.task,
                    source_text=self.task.render_sources(updated=True),
                    prior_final_output=prior_final,
                    prior_intermediates=None,
                    edits=(self.task.primary_edit,),
                ),
            ),
            (
                prompt_module.load_prompt("loop_real_world/with_notes.md"),
                runner._build_real_world_update_message(
                    task_bundle=self.task,
                    source_text=self.task.render_sources(updated=True),
                    prior_final_output=prior_final,
                    prior_intermediates=prior_intermediates,
                    edits=(),
                ),
            ),
            (
                prompt_module.load_prompt("loop_real_world/with_memory.md"),
                runner._build_real_world_update_message(
                    task_bundle=self.task,
                    source_text=self.task.render_sources(updated=True),
                    prior_final_output=prior_final,
                    prior_intermediates=None,
                    edits=(),
                ),
            ),
            (
                prompt_module.load_prompt("loop_real_world/staged_update.md"),
                runner._build_step_user_message(
                    task_bundle=self.task,
                    source_text=self.task.render_sources_for_stage(updated=True, stage_name="reimbursement_context"),
                    step_prompt=prompt_module.load_prompt("loop_staged/reimbursement_context.md"),
                    prior_final_output=prior_final,
                    prior_intermediates=None,
                    edit=(self.task.primary_edit,),
                ),
            ),
        ]

        for instruction, message in messages_and_instructions:
            combined = f"{instruction}\n\n{message}"
            for needle in FORBIDDEN_LOOP_STRINGS:
                if "with_edit_event.md" in instruction and needle in {"Old source id:", "New source id:"}:
                    continue
                self.assertNotIn(needle, combined)

    def test_loop_real_world_with_edit_event_includes_only_source_replacement_event(self) -> None:
        runner = make_loop_runner()
        prompt = prompt_module.load_prompt("loop_real_world/with_edit_event.md")
        message = runner._build_real_world_update_message(
            task_bundle=self.task,
            source_text=self.task.render_sources(updated=True),
            prior_final_output="Prior memo text",
            prior_intermediates=None,
            edits=(self.task.primary_edit,),
        )
        combined = f"{prompt}\n\n{message}"
        self.assertIn(self.task.primary_edit.old_source_id, combined)
        self.assertIn(self.task.primary_edit.new_source_id, combined)
        self.assertIn("Previous final memo:", combined)
        self.assertIn("Current source materials:", combined)
        self.assertIn("Task:", combined)
        self.assertIn("Requested output format:", combined)
        self.assertNotIn("Declared dependency artifacts:", combined)
        self.assertNotIn("expected_affected_claim_ids", combined)
        self.assertNotIn("expected_unaffected_claim_ids", combined)
        self.assertNotIn("expected_recomputed_stages", combined)

    def test_loop_real_world_final_update_remains_without_edit_event(self) -> None:
        runner = make_loop_runner()
        prompt = prompt_module.load_prompt("loop_real_world/final_update.md")
        message = runner._build_real_world_update_message(
            task_bundle=self.task,
            source_text=self.task.render_sources(updated=True),
            prior_final_output="Prior memo text",
            prior_intermediates=None,
            edits=(),
        )
        combined = f"{prompt}\n\n{message}"
        self.assertNotIn("Source update event:", combined)
        self.assertNotIn(f"source `{self.task.primary_edit.old_source_id}` was replaced by source `{self.task.primary_edit.new_source_id}`", combined)

    def test_multi_edit_task_routes_versioned_sources_by_stage(self) -> None:
        task = load_task(MULTI_EDIT_TASK_FILE)
        operations = task.render_sources_for_stage(updated=True, stage_name="operations_context")
        access_cost = task.render_sources_for_stage(updated=True, stage_name="access_cost_context")

        self.assertIn("[S3_current]", operations)
        self.assertNotIn("[S3_old]", operations)
        self.assertIn("[S4_current]", access_cost)
        self.assertIn("[S5_current]", access_cost)

    def test_intermediate_edit_loop_prompt_uses_human_edit_note_not_graph_metadata(self) -> None:
        task = load_task(INTERMEDIATE_EDIT_TASK_FILE)
        runner = make_loop_runner()
        message = runner._build_real_world_update_message(
            task_bundle=task,
            source_text=task.render_sources(updated=True),
            prior_final_output="Prior memo text",
            prior_intermediates=None,
            edits=task.update_edits,
        )
        self.assertIn("budget-neutral", message)
        self.assertIn("recommendation criteria", message)
        self.assertNotIn("expected_recomputed_stages", message)
        self.assertNotIn("Declared dependency artifacts:", message)

    def test_rendered_prompts_are_written_as_artifacts(self) -> None:
        config = RunnerConfig(
            model_provider="openai",
            model_name="gpt-test",
            model_temperature=0.0,
            model_seed=None,
            default_conditions=("loop_centric_fresh",),
            optional_conditions=(),
            disabled_conditions=(),
            loop_context_strategy="rolling_summary_plus_memory_tool",
            chatgpt_url="https://chatgpt.com/",
            chatgpt_profile_dir=Path("."),
            chatgpt_profile_name=None,
            chatgpt_timeout_s=1.0,
            thruwire_model_provider="openai",
            keep_thruwire_project=False,
            chatgpt_browser_channel="chrome",
            chatgpt_cdp_url="http://127.0.0.1:9222",
            openai_eval_model="gpt-test",
            run_openai_evaluation=False,
            loop_summary_trigger_tokens=1000,
            loop_summary_keep_messages=2,
            openai_max_retries=1,
            openai_retry_base_delay_ms=1,
            raw_config={},
        )
        results = {
            "loop_centric_fresh": {
                "condition_name": "loop_centric_fresh",
                "runs": [
                    {
                        "prompt_response_log": [
                            {
                                "step_name": "final_memo",
                                "instructions": "system text",
                                "input_items": [{"type": "message", "role": "user", "content": "hello"}],
                                "response_id": "resp_1",
                            }
                        ]
                    }
                ],
            }
        }
        bundle = _build_bundle(self.task, config, results)
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            _write_rendered_prompts(output_dir, bundle)
            artifact = output_dir / "rendered_prompts" / "loop_centric_fresh" / "run_01" / "01_final_memo.md"
            self.assertTrue(artifact.exists())
            self.assertIn("system text", artifact.read_text())


if __name__ == "__main__":
    unittest.main()
