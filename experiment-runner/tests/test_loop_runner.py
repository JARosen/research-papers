from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from experiment_runner.harnesses.loop_centric.memory import TransparentMemoryStore
from experiment_runner.harnesses.loop_centric.runner import LoopCentricHarnessRunner
from experiment_runner.harnesses.loop_centric.schemas import blank_memory_metadata
from experiment_runner.harnesses.loop_centric.transcript import Transcript
from experiment_runner.tasks import load_task


TASK_FILE = Path("experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1/task.json")


class FakeModelClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def generate_items(self, **kwargs):
        if not self._responses:
            raise AssertionError("No fake responses remaining")
        response = dict(self._responses.pop(0))
        response.setdefault("step_name", kwargs.get("step_name"))
        response.setdefault("duration_ms", 1)
        response.setdefault("input_tokens", 10)
        response.setdefault("output_tokens", 5)
        response.setdefault("response_id", f"resp_{len(self._responses)}")
        response.setdefault("output_items", [])
        response.setdefault("text", "")
        return response


def assistant_output_item(text: str, *, item_id: str = "msg_1") -> dict:
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


class LoopRunnerTests(unittest.TestCase):
    def _make_runner(self, responses, *, summary_trigger_tokens: int = 10_000) -> LoopCentricHarnessRunner:
        runner = LoopCentricHarnessRunner.__new__(LoopCentricHarnessRunner)
        runner.config = SimpleNamespace(
            model_name="gpt-test",
            model_provider="openai",
            loop_context_strategy="rolling_summary_plus_memory_tool",
            loop_summary_trigger_tokens=summary_trigger_tokens,
            loop_summary_keep_messages=2,
        )
        runner.model_client = FakeModelClient(responses)
        return runner

    def test_transcript_summary_replaces_older_messages(self) -> None:
        transcript = Transcript()
        transcript.add_message(role="user", content="u1", step_name="s1", model="m", provider="p")
        transcript.add_message(role="assistant", content="a1", step_name="s1", model="m", provider="p")
        transcript.add_message(role="user", content="u2", step_name="s2", model="m", provider="p")
        transcript.add_message(role="assistant", content="a2", step_name="s2", model="m", provider="p")

        transcript.apply_summary(summary_text="summary", keep_last_messages=2)
        items = transcript.current_input_items()

        self.assertEqual(items[0]["role"], "assistant")
        self.assertEqual(items[0]["content"], "summary")
        self.assertEqual(items[0]["phase"], "commentary")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[1]["content"], "u2")

    def test_tool_items_do_not_carry_forward_to_later_steps(self) -> None:
        transcript = Transcript()
        transcript.add_message(role="user", content="user", step_name="source_set", model="m", provider="p")
        transcript.add_item(
            item={"type": "function_call", "call_id": "call_1", "name": "memory_list", "arguments": "{}"},
            step_name="source_set",
            model="m",
            provider="p",
            carry_forward=False,
        )
        transcript.add_item(
            item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
            step_name="source_set",
            model="m",
            provider="p",
            carry_forward=False,
        )
        transcript.add_output_items(
            items=[assistant_output_item("done", item_id="msg_done")],
            step_name="source_set",
            model="m",
            provider="p",
            carry_forward=False,
        )

        same_step_items = transcript.current_input_items(current_step_name="source_set")
        next_step_items = transcript.current_input_items(current_step_name="evidence_digest")

        self.assertTrue(any(item.get("type") == "function_call" for item in same_step_items))
        self.assertFalse(any(item.get("type") == "function_call" for item in next_step_items))
        self.assertFalse(any(item.get("type") == "function_call_output" for item in next_step_items))

    def test_execute_tool_calls_updates_memory_metadata(self) -> None:
        runner = self._make_runner([])
        task = load_task(TASK_FILE)
        store = TransparentMemoryStore(task.memory_file)
        metadata = blank_memory_metadata(uses_memory=True)

        outputs = runner._execute_tool_calls(
            [
                {"type": "function_call", "call_id": "call_1", "name": "memory_list", "arguments": "{}"},
                {"type": "function_call", "call_id": "call_2", "name": "memory_get", "arguments": '{"id":"workflow_recipe_v1"}'},
            ],
            memory_store=store,
            memory_metadata=metadata,
            step_name="claim_matrix",
        )

        self.assertEqual(outputs[0]["type"], "function_call_output")
        self.assertIn("workflow_recipe_v1", metadata["memory_entry_ids_available"])
        self.assertIn("workflow_recipe_v1", metadata["memory_entry_ids_retrieved"])
        self.assertEqual(len(metadata["memory_tool_calls"]), 2)

    def test_run_step_with_tools_executes_tool_loop(self) -> None:
        runner = self._make_runner(
            [
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
                    "text": "final step text",
                    "output_items": [assistant_output_item("final step text", item_id="msg_final")],
                },
            ]
        )
        task = load_task(TASK_FILE)
        store = TransparentMemoryStore(task.memory_file)
        transcript = Transcript()
        transcript.add_message(role="user", content="step prompt", step_name="source_set", model="m", provider="p")
        metadata = blank_memory_metadata(uses_memory=True)
        prompt_log: list[dict] = []

        result = runner._run_step_with_tools(
            transcript=transcript,
            instructions="system instructions",
            step_name="source_set",
            memory_store=store,
            memory_metadata=metadata,
            prompt_response_log=prompt_log,
        )

        self.assertEqual(result["text"], "final step text")
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(len(prompt_log), 2)
        self.assertTrue(any(item["tool_name"] == "memory_list" for item in metadata["memory_tool_calls"]))

    def test_execute_loop_runs_without_network(self) -> None:
        responses = [
            {"text": f"result {idx}", "output_items": [assistant_output_item(f"result {idx}", item_id=f"msg_{idx}")]}
            for idx in range(8)
        ]
        runner = self._make_runner(responses)
        task = load_task(TASK_FILE)
        metadata = blank_memory_metadata(uses_memory=False)

        result = runner._execute_loop(
            task_bundle=task,
            transcript=Transcript(),
            source_text=task.render_sources(updated=False),
            memory_metadata=metadata,
            memory_store=TransparentMemoryStore(None),
            instructions="system instructions",
        )

        self.assertEqual(result["final_output"], "result 7")
        self.assertEqual(len(result["intermediate_outputs"]), 8)
        self.assertEqual(len(result["prompt_response_log"]), 8)
        self.assertEqual(result["model_usage"]["model_calls"], 8)


if __name__ == "__main__":
    unittest.main()
