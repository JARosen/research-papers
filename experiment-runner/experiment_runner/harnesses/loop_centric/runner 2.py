from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from experiment_runner.config import RunnerConfig
from experiment_runner.harnesses.base import HarnessRunResult
from experiment_runner.harnesses.common import OpenAITextModelClient, new_run_id, sha256_text, utc_now
from experiment_runner.harnesses.loop_centric.memory import TransparentMemoryStore
from experiment_runner.harnesses.loop_centric.prompts import load_prompt
from experiment_runner.harnesses.loop_centric.schemas import blank_memory_metadata
from experiment_runner.harnesses.loop_centric.transcript import Transcript
from experiment_runner.tasks import ContextDisciplineTask, UpstreamEdit


STEP_SEQUENCE = [
    ("source_set", "loop_source_set.md"),
    ("evidence_digest", "loop_evidence_digest.md"),
    ("claim_matrix", "loop_claim_matrix.md"),
    ("tension_analysis", "loop_tension_analysis.md"),
    ("recommendation_criteria", "loop_recommendation_criteria.md"),
    ("final_memo", "loop_final_memo.md"),
]


class LoopCentricHarnessRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.model_client = OpenAITextModelClient(config)

    def run_fresh(self, task_bundle: ContextDisciplineTask, *, repeats: int = 1) -> HarnessRunResult:
        runs = [self._run_single_fresh(task_bundle, use_procedural_memory=False) for _ in range(repeats)]
        return HarnessRunResult(
            payload={
                "condition_id": "C1",
                "condition_name": "loop_centric_fresh",
                "runs": runs,
                "final_output": runs[-1]["final_output"] if runs else "",
            }
        )

    def run_fresh_with_procedural_memory(self, task_bundle: ContextDisciplineTask, *, repeats: int = 1) -> HarnessRunResult:
        runs = [self._run_single_fresh(task_bundle, use_procedural_memory=True) for _ in range(repeats)]
        return HarnessRunResult(
            payload={
                "condition_name": "loop_centric_with_procedural_memory",
                "runs": runs,
                "final_output": runs[-1]["final_output"] if runs else "",
            }
        )

    def run_update(
        self,
        task_bundle: ContextDisciplineTask,
        prior_run: HarnessRunResult,
        edit: UpstreamEdit,
        *,
        include_intermediates: bool = False,
        use_procedural_memory: bool = False,
    ) -> HarnessRunResult:
        prior_payload = prior_run.payload["runs"][0] if "runs" in prior_run.payload else prior_run.payload
        result = self._run_update(
            task_bundle,
            prior_final_output=str(prior_payload["final_output"]),
            prior_intermediates=prior_payload.get("intermediate_outputs", []),
            edit=edit,
            include_intermediates=include_intermediates,
            use_procedural_memory=use_procedural_memory,
        )
        return HarnessRunResult(payload=result)

    def _run_single_fresh(self, task_bundle: ContextDisciplineTask, *, use_procedural_memory: bool) -> dict[str, Any]:
        memory_file = task_bundle.task_dir / "memory.json"
        memory_store = TransparentMemoryStore(memory_file if use_procedural_memory else None)
        transcript = Transcript()
        memory_metadata = blank_memory_metadata(uses_memory=use_procedural_memory)
        if use_procedural_memory:
            memory_metadata["memory_entry_ids_available"] = memory_store.available_ids()
            retrieved = memory_store.retrieve(
                query="workflow recipe current sources superseded source notes final memo",
                include_prior_artifacts=False,
            )
            memory_metadata["memory_entry_ids_retrieved"] = retrieved["memory_entry_ids_retrieved"]
            memory_metadata["retrieval_queries"] = [retrieved["retrieval_query"]]
            memory_metadata["retrieved_context"] = retrieved["retrieved_context"]
        return self._execute_loop(
            task_bundle=task_bundle,
            transcript=transcript,
            source_text=task_bundle.render_sources(updated=False),
            memory_metadata=memory_metadata,
            prompt_name="loop_centric_fresh.md" if not use_procedural_memory else "loop_with_procedural_memory.md",
        )

    def _run_update(
        self,
        task_bundle: ContextDisciplineTask,
        *,
        prior_final_output: str,
        prior_intermediates: list[dict[str, Any]],
        edit: UpstreamEdit,
        include_intermediates: bool,
        use_procedural_memory: bool,
    ) -> dict[str, Any]:
        transcript = Transcript()
        memory_file = task_bundle.task_dir / "memory.json"
        memory_store = TransparentMemoryStore(memory_file if use_procedural_memory else None)
        memory_metadata = blank_memory_metadata(uses_memory=use_procedural_memory)
        if use_procedural_memory:
            memory_metadata["memory_entry_ids_available"] = memory_store.available_ids()
            retrieved = memory_store.retrieve(
                query=f"workflow recipe update current versions superseded source {edit.old_source_id} {edit.new_source_id}",
                include_prior_artifacts=True,
            )
            memory_metadata["memory_entry_ids_retrieved"] = retrieved["memory_entry_ids_retrieved"]
            memory_metadata["retrieval_queries"] = [retrieved["retrieval_query"]]
            memory_metadata["retrieved_context"] = retrieved["retrieved_context"]

        prompt_name = "loop_update_final_only.md"
        if include_intermediates:
            prompt_name = "loop_update_with_intermediates.md"
        if use_procedural_memory:
            prompt_name = "loop_with_procedural_memory.md"

        manual_context_reconstruction_actions = 0
        if include_intermediates:
            manual_context_reconstruction_actions = len(prior_intermediates)
        elif not use_procedural_memory:
            manual_context_reconstruction_actions = 1

        return self._execute_loop(
            task_bundle=task_bundle,
            transcript=transcript,
            source_text=task_bundle.render_sources(updated=True),
            memory_metadata=memory_metadata,
            prompt_name=prompt_name,
            prior_final_output=prior_final_output,
            prior_intermediates=prior_intermediates,
            edit=edit,
            manual_context_reconstruction_actions=manual_context_reconstruction_actions,
        )

    def _execute_loop(
        self,
        *,
        task_bundle: ContextDisciplineTask,
        transcript: Transcript,
        source_text: str,
        memory_metadata: dict[str, Any],
        prompt_name: str,
        prior_final_output: str | None = None,
        prior_intermediates: list[dict[str, Any]] | None = None,
        edit: UpstreamEdit | None = None,
        manual_context_reconstruction_actions: int = 0,
    ) -> dict[str, Any]:
        run_id = new_run_id("loop")
        started_at = utc_now()
        run_start = time.perf_counter()
        system_prompt = load_prompt(prompt_name)
        transcript.add(
            role="system",
            content=system_prompt,
            step_name="setup",
            model=self.config.model_name,
            provider=self.config.model_provider,
        )

        assembled_context = [
            f"Task: {task_bundle.title}",
            f"Instruction:\n{task_bundle.instruction}",
            f"Output requirements:\n{task_bundle.requested_output_format}",
            f"Source bundle:\n{source_text}",
        ]
        if prior_final_output:
            assembled_context.append(f"Prior final output:\n{prior_final_output}")
        if prior_intermediates:
            rendered = []
            for item in prior_intermediates:
                rendered.append(f"[{item.get('name')}]\n{item.get('content')}")
            assembled_context.append("Prior intermediate notes/artifacts:\n" + "\n\n".join(rendered))
        if edit is not None:
            assembled_context.append(
                f"Upstream edit:\n{edit.description}\nOld source id: {edit.old_source_id}\nNew source id: {edit.new_source_id}"
            )
        if memory_metadata.get("retrieved_context"):
            assembled_context.append("Retrieved procedural memory:\n" + "\n\n".join(memory_metadata["retrieved_context"]))

        base_context = "\n\n".join(assembled_context)
        intermediate_outputs: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0

        for step_name, prompt_file in STEP_SEQUENCE:
            user_prompt = (
                load_prompt(prompt_file)
                + "\n\n"
                + "Conversation history:\n"
                + transcript.render_full_transcript()
                + "\n\n"
                + "Working context:\n"
                + base_context
            )
            transcript.add(
                role="user",
                content=user_prompt,
                step_name=step_name,
                model=self.config.model_name,
                provider=self.config.model_provider,
            )
            response = self.model_client.generate(prompt=user_prompt, step_name=step_name)
            total_input_tokens += response["input_tokens"]
            total_output_tokens += response["output_tokens"]
            transcript.add(
                role="assistant",
                content=response["text"],
                step_name=step_name,
                model=self.config.model_name,
                provider=self.config.model_provider,
                input_tokens=response["input_tokens"],
                output_tokens=response["output_tokens"],
                duration_ms=response["duration_ms"],
            )
            intermediate_outputs.append(
                {
                    "name": step_name,
                    "content": response["text"],
                    "identity": None,
                    "hash": sha256_text(response["text"]),
                }
            )

        final_output = intermediate_outputs[-1]["content"] if intermediate_outputs else ""
        duration_ms = int((time.perf_counter() - run_start) * 1000)
        return {
            "run_id": run_id,
            "task_id": task_bundle.task_id,
            "model": self.config.model_name,
            "provider": self.config.model_provider,
            "started_at": started_at,
            "ended_at": utc_now(),
            "duration_ms": duration_ms,
            "final_output": final_output,
            "intermediate_outputs": intermediate_outputs,
            "conversation_transcript": transcript.to_list(),
            "prompt_response_log": transcript.to_list(),
            "memory_metadata": memory_metadata,
            "execution_metadata": {
                "harness_type": "loop_centric",
                "uses_graph_dependencies": False,
                "uses_execution_identity": False,
                "uses_replay": False,
                "uses_automatic_invalidation": False,
                "uses_persistent_product_memory": False,
                "context_strategy": self.config.loop_context_strategy,
                "manual_context_reconstruction_actions": manual_context_reconstruction_actions,
            },
            "model_usage": {
                "model_calls": len(STEP_SEQUENCE),
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
        }
