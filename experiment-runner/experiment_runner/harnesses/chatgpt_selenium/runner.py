from __future__ import annotations

from experiment_runner.chatgpt_backend import ChatGPTBaselineRunner
from experiment_runner.config import RunnerConfig
from experiment_runner.harnesses.base import HarnessRunResult
from experiment_runner.harnesses.common import new_run_id, sha256_text, utc_now
from experiment_runner.tasks import ContextDisciplineTask, UpstreamEdit


class ChatGPTProductSeleniumRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.runner = ChatGPTBaselineRunner(config)

    def run_fresh(self, task_bundle: ContextDisciplineTask, *, repeats: int = 1) -> HarnessRunResult:
        runs = self.runner.run_repeated_trials([task_bundle.loop_fresh_prompt() for _ in range(repeats)])
        payload_runs = []
        for item in runs:
            payload_runs.append(
                {
                    "run_id": new_run_id("selenium"),
                    "task_id": task_bundle.task_id,
                    "condition": "chatgpt_product_selenium",
                    "model": "chatgpt_product_hidden",
                    "provider": "chatgpt_product",
                    "started_at": utc_now(),
                    "ended_at": utc_now(),
                    "duration_ms": int(item.duration_s * 1000),
                    "final_output": item.final_text,
                    "intermediate_outputs": [],
                    "conversation_transcript": [],
                    "prompt_response_log": [
                        {"role": "user", "content": item.prompt, "step_name": "final_memo"},
                        {"role": "assistant", "content": item.final_text, "step_name": "final_memo"},
                    ],
                    "memory_metadata": {
                        "uses_memory": True,
                        "memory_entry_ids_available": [],
                        "memory_entry_ids_retrieved": [],
                        "retrieval_queries": [],
                        "retrieved_context": [],
                    },
                    "execution_metadata": {
                        "harness_type": "chatgpt_product_selenium",
                        "uses_graph_dependencies": False,
                        "uses_execution_identity": False,
                        "uses_replay": False,
                        "uses_automatic_invalidation": False,
                        "uses_persistent_product_memory": True,
                        "context_strategy": "hidden_product_context",
                        "manual_context_reconstruction_actions": 0,
                    },
                    "model_usage": {
                        "model_calls": 1,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                    "artifact_hash": sha256_text(item.final_text),
                    "conversation_url": item.conversation_url,
                }
            )
        return HarnessRunResult(payload={"condition_name": "chatgpt_product_selenium", "runs": payload_runs})

    def run_update(
        self,
        task_bundle: ContextDisciplineTask,
        prior_run: HarnessRunResult,
        edit: UpstreamEdit,
        *,
        include_intermediates: bool = False,
        use_procedural_memory: bool = False,
    ) -> HarnessRunResult:
        raise NotImplementedError("Optional Selenium harness is preserved for future ecological/product baselines.")
