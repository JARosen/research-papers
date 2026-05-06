from __future__ import annotations

import json
import time
import uuid
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
    ("utilization_context", "loop_utilization_context.md"),
    ("reimbursement_context", "loop_reimbursement_context.md"),
    ("operations_context", "loop_operations_context.md"),
    ("access_cost_context", "loop_access_cost_context.md"),
    ("claim_matrix", "loop_claim_matrix.md"),
    ("tension_analysis", "loop_tension_analysis.md"),
    ("recommendation_criteria", "loop_recommendation_criteria.md"),
    ("final_memo", "loop_final_memo.md"),
]

REAL_WORLD_UPDATE_STEP = "final_memo_update"

SUMMARY_PROMPT = """Summarize the earlier workflow history for a long-running loop.

Produce a compact but information-dense state summary for future workflow steps.
Preserve only durable information that later steps may need:
- accepted evidence and explicit exclusions/decoys
- source-version state, replacements, and stale-source warnings
- working claims, counterclaims, and uncertainties
- unresolved tensions, risks, and caveats that must survive later drafting
- decisions already made about scope, framing, or recommendation logic
- any constraints from the task or output requirements that materially affect later steps

Do not preserve raw tool chatter or boilerplate.
Do not invent facts.
Prefer crisp bullets or short labeled sections over prose.
Keep enough detail to safely replace many prior turns, but stay concise."""

MEMORY_TOOLS = [
    {
        "type": "function",
        "name": "memory_list",
        "description": "List available compressed memory-note ids and return the advisory memory index.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "memory_get",
        "description": "Fetch one compressed memory note by id. Memories may be partial, lossy, stale, or blended summaries rather than exact workflow artifacts.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The exact memory note id to fetch.",
                }
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
]


class LoopCentricHarnessRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.model_client = OpenAITextModelClient(config)

    def run_fresh(self, task_bundle: ContextDisciplineTask, *, repeats: int = 1) -> HarnessRunResult:
        runs = []
        for index in range(repeats):
            print(f"[loop] fresh run {index + 1}/{repeats}")
            runs.append(self._run_single_fresh(task_bundle, use_procedural_memory=False))
        return HarnessRunResult(
            payload={
                "condition_id": "C1",
                "condition_name": "loop_centric_fresh",
                "runs": runs,
                "final_output": runs[-1]["final_output"] if runs else "",
            }
        )

    def run_fresh_with_procedural_memory(self, task_bundle: ContextDisciplineTask, *, repeats: int = 1) -> HarnessRunResult:
        runs = []
        for index in range(repeats):
            print(f"[loop-memory] fresh run {index + 1}/{repeats}")
            runs.append(self._run_single_fresh(task_bundle, use_procedural_memory=True))
        return HarnessRunResult(
            payload={
                "condition_name": "loop_real_world_with_memory",
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
        mode = "loop-memory-update" if use_procedural_memory else ("loop-update-with-intermediates" if include_intermediates else "loop-update-final-only")
        print(f"[{mode}] starting update run")
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

    def run_staged_update(
        self,
        task_bundle: ContextDisciplineTask,
        prior_run: HarnessRunResult,
        edit: UpstreamEdit,
    ) -> HarnessRunResult:
        print("[loop-staged-update] starting update run")
        prior_payload = prior_run.payload["runs"][0] if "runs" in prior_run.payload else prior_run.payload
        result = self._execute_loop(
            task_bundle=task_bundle,
            transcript=Transcript(),
            source_text=task_bundle.render_sources(updated=True),
            memory_metadata=blank_memory_metadata(uses_memory=False),
            memory_store=TransparentMemoryStore(None),
            instructions=load_prompt("loop_real_world_staged_update.md"),
            prior_final_output=str(prior_payload["final_output"]),
            edit=edit,
            manual_context_reconstruction_actions=1,
        )
        return HarnessRunResult(payload=result)

    def _run_single_fresh(self, task_bundle: ContextDisciplineTask, *, use_procedural_memory: bool) -> dict[str, Any]:
        memory_store = TransparentMemoryStore(task_bundle.memory_file if use_procedural_memory else None)
        memory_metadata = blank_memory_metadata(uses_memory=use_procedural_memory)
        if use_procedural_memory:
            listed = memory_store.list()
            memory_metadata["memory_entry_ids_available"] = listed["memory_entry_ids_available"]
            memory_metadata["memory_index"] = listed["memory_index"]
        return self._execute_loop(
            task_bundle=task_bundle,
            transcript=Transcript(),
            source_text=task_bundle.render_sources(updated=False),
            memory_metadata=memory_metadata,
            memory_store=memory_store,
            instructions=load_prompt("loop_centric_fresh.md" if not use_procedural_memory else "loop_with_procedural_memory.md"),
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
        memory_store = TransparentMemoryStore(task_bundle.memory_file if use_procedural_memory else None)
        memory_metadata = blank_memory_metadata(uses_memory=use_procedural_memory)
        if use_procedural_memory:
            listed = memory_store.list()
            memory_metadata["memory_entry_ids_available"] = listed["memory_entry_ids_available"]
            memory_metadata["memory_index"] = listed["memory_index"]

        manual_context_reconstruction_actions = 0
        if include_intermediates:
            manual_context_reconstruction_actions = len(prior_intermediates)
        elif not use_procedural_memory:
            manual_context_reconstruction_actions = 1

        prompt_name = "loop_real_world_final_update.md"
        if include_intermediates:
            prompt_name = "loop_real_world_with_notes.md"
        if use_procedural_memory:
            prompt_name = "loop_real_world_with_memory.md"

        return self._execute_real_world_update(
            task_bundle=task_bundle,
            transcript=Transcript(),
            source_text=task_bundle.render_sources(updated=True),
            memory_metadata=memory_metadata,
            memory_store=memory_store,
            instructions=load_prompt(prompt_name),
            prior_final_output=prior_final_output,
            prior_intermediates=prior_intermediates,
            manual_context_reconstruction_actions=manual_context_reconstruction_actions,
        )

    def _execute_real_world_update(
        self,
        *,
        task_bundle: ContextDisciplineTask,
        transcript: Transcript,
        source_text: str,
        memory_metadata: dict[str, Any],
        memory_store: TransparentMemoryStore,
        instructions: str,
        prior_final_output: str,
        prior_intermediates: list[dict[str, Any]] | None,
        manual_context_reconstruction_actions: int,
    ) -> dict[str, Any]:
        run_id = new_run_id("loop")
        started_at = utc_now()
        run_start = time.perf_counter()
        prompt_response_log: list[dict[str, Any]] = []

        user_content = self._build_real_world_update_message(
            task_bundle=task_bundle,
            source_text=source_text,
            prior_final_output=prior_final_output,
            prior_intermediates=prior_intermediates,
        )
        transcript.add_message(
            role="user",
            content=user_content,
            step_name=REAL_WORLD_UPDATE_STEP,
            model=self.config.model_name,
            provider=self.config.model_provider,
        )

        step_result = self._run_step_with_tools(
            transcript=transcript,
            instructions=instructions,
            step_name=REAL_WORLD_UPDATE_STEP,
            memory_store=memory_store,
            memory_metadata=memory_metadata,
            prompt_response_log=prompt_response_log,
        )
        duration_ms = int((time.perf_counter() - run_start) * 1000)
        final_output = step_result["text"]

        return {
            "run_id": run_id,
            "task_id": task_bundle.task_id,
            "model": self.config.model_name,
            "provider": self.config.model_provider,
            "started_at": started_at,
            "ended_at": utc_now(),
            "duration_ms": duration_ms,
            "final_output": final_output,
            "intermediate_outputs": [
                {
                    "name": "final_memo",
                    "content": final_output,
                    "identity": None,
                    "hash": sha256_text(final_output),
                }
            ],
            "conversation_transcript": transcript.to_list(),
            "prompt_response_log": prompt_response_log,
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
                "model_calls": step_result["model_calls"],
                "input_tokens": step_result["input_tokens"],
                "output_tokens": step_result["output_tokens"],
            },
        }

    def _execute_loop(
        self,
        *,
        task_bundle: ContextDisciplineTask,
        transcript: Transcript,
        source_text: str,
        memory_metadata: dict[str, Any],
        memory_store: TransparentMemoryStore,
        instructions: str,
        prior_final_output: str | None = None,
        prior_intermediates: list[dict[str, Any]] | None = None,
        edit: UpstreamEdit | None = None,
        manual_context_reconstruction_actions: int = 0,
    ) -> dict[str, Any]:
        run_id = new_run_id("loop")
        started_at = utc_now()
        run_start = time.perf_counter()
        intermediate_outputs: list[dict[str, Any]] = []
        prompt_response_log: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0
        auxiliary_model_calls = 0

        for step_name, prompt_file in STEP_SEQUENCE:
            print(f"[loop] step {step_name}")
            summary_result = self._maybe_summarize_transcript(
                transcript=transcript,
                prompt_response_log=prompt_response_log,
                memory_metadata=memory_metadata,
            )
            total_input_tokens += summary_result["input_tokens"]
            total_output_tokens += summary_result["output_tokens"]
            auxiliary_model_calls += summary_result["model_calls"]

            user_content = self._build_step_user_message(
                task_bundle=task_bundle,
                source_text=task_bundle.render_sources_for_stage(updated=edit is not None, stage_name=step_name),
                step_prompt=load_prompt(prompt_file),
                prior_final_output=prior_final_output,
                prior_intermediates=prior_intermediates,
                edit=edit,
            )
            transcript.add_message(
                role="user",
                content=user_content,
                step_name=step_name,
                model=self.config.model_name,
                provider=self.config.model_provider,
            )

            step_result = self._run_step_with_tools(
                transcript=transcript,
                instructions=instructions,
                step_name=step_name,
                memory_store=memory_store,
                memory_metadata=memory_metadata,
                prompt_response_log=prompt_response_log,
            )
            total_input_tokens += step_result["input_tokens"]
            total_output_tokens += step_result["output_tokens"]
            auxiliary_model_calls += step_result["model_calls"] - 1
            print(
                f"[loop] completed {step_name} "
                f"({step_result['input_tokens']} in / {step_result['output_tokens']} out, {step_result['duration_ms']} ms)"
            )
            intermediate_outputs.append(
                {
                    "name": step_name,
                    "content": step_result["text"],
                    "identity": None,
                    "hash": sha256_text(step_result["text"]),
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
            "prompt_response_log": prompt_response_log,
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
                "model_calls": len(STEP_SEQUENCE) + auxiliary_model_calls,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
        }

    def _build_step_user_message(
        self,
        *,
        task_bundle: ContextDisciplineTask,
        source_text: str,
        step_prompt: str,
        prior_final_output: str | None,
        prior_intermediates: list[dict[str, Any]] | None,
        edit: UpstreamEdit | None,
    ) -> str:
        parts = [
            f"Task: {task_bundle.title}",
            f"Instruction:\n{task_bundle.instruction}",
            f"Output requirements:\n{task_bundle.requested_output_format}",
            f"Current workflow step instructions:\n{step_prompt}",
            f"Source bundle:\n{source_text}",
        ]
        if prior_final_output:
            parts.append(f"Prior final output:\n{prior_final_output}")
        if prior_intermediates:
            rendered = []
            for item in prior_intermediates:
                rendered.append(f"[{item.get('name')}]\n{item.get('content')}")
            parts.append("Prior intermediate notes/artifacts:\n" + "\n\n".join(rendered))
        return "\n\n".join(parts)

    def _build_real_world_update_message(
        self,
        *,
        task_bundle: ContextDisciplineTask,
        source_text: str,
        prior_final_output: str,
        prior_intermediates: list[dict[str, Any]] | None,
    ) -> str:
        parts = [
            f"Previous final memo:\n{prior_final_output}",
        ]
        if prior_intermediates:
            rendered = []
            for item in prior_intermediates:
                rendered.append(f"[{item.get('name')}]\n{item.get('content')}")
            parts.append("Prior working notes:\n" + "\n\n".join(rendered))
        parts.extend(
            [
                f"Current source materials:\n{source_text}",
                f"Task:\n{task_bundle.instruction}",
                f"Requested output format:\n{task_bundle.requested_output_format}",
            ]
        )
        return "\n\n".join(parts)

    def _run_step_with_tools(
        self,
        *,
        transcript: Transcript,
        instructions: str,
        step_name: str,
        memory_store: TransparentMemoryStore,
        memory_metadata: dict[str, Any],
        prompt_response_log: list[dict[str, Any]],
    ) -> dict[str, Any]:
        aggregate_input_tokens = 0
        aggregate_output_tokens = 0
        aggregate_duration_ms = 0
        call_count = 0
        latest_text = ""

        while True:
            input_items = transcript.current_input_items(current_step_name=step_name)
            response = self.model_client.generate_items(
                instructions=instructions,
                input_items=input_items,
                step_name=step_name,
                tools=MEMORY_TOOLS if memory_metadata.get("uses_memory") else None,
                tool_choice="auto" if memory_metadata.get("uses_memory") else None,
            )
            call_count += 1
            aggregate_input_tokens += response["input_tokens"]
            aggregate_output_tokens += response["output_tokens"]
            aggregate_duration_ms += response["duration_ms"]
            latest_text = response["text"]
            prompt_response_log.append(
                {
                    "step_name": step_name,
                    "instructions": instructions,
                    "input_items": input_items,
                    "output_items": response["output_items"],
                    "response": response["text"],
                    "input_tokens": response["input_tokens"],
                    "output_tokens": response["output_tokens"],
                    "duration_ms": response["duration_ms"],
                    "response_id": response["response_id"],
                }
            )
            transcript.add_output_items(
                items=response["output_items"],
                step_name=step_name,
                model=self.config.model_name,
                provider=self.config.model_provider,
                input_tokens=response["input_tokens"],
                output_tokens=response["output_tokens"],
                duration_ms=response["duration_ms"],
                carry_forward=False,
            )
            function_calls = [item for item in response["output_items"] if item.get("type") == "function_call"]
            if not function_calls:
                if memory_metadata.get("uses_memory"):
                    print(f"[loop-memory] no tool calls for {step_name}")
                break
            if memory_metadata.get("uses_memory"):
                print(f"[loop-memory] {step_name} requested {len(function_calls)} tool call(s)")
            for tool_output in self._execute_tool_calls(function_calls, memory_store=memory_store, memory_metadata=memory_metadata, step_name=step_name):
                transcript.add_item(
                    item=tool_output,
                    step_name=step_name,
                    model=self.config.model_name,
                    provider=self.config.model_provider,
                    carry_forward=False,
                )

        return {
            "text": latest_text.strip(),
            "input_tokens": aggregate_input_tokens,
            "output_tokens": aggregate_output_tokens,
            "duration_ms": aggregate_duration_ms,
            "model_calls": call_count,
        }

    def _execute_tool_calls(
        self,
        function_calls: list[dict[str, Any]],
        *,
        memory_store: TransparentMemoryStore,
        memory_metadata: dict[str, Any],
        step_name: str,
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for call in function_calls:
            name = str(call.get("name", ""))
            arguments = self._parse_tool_arguments(str(call.get("arguments", "{}")))
            print(f"[loop-memory] tool {name} args={json.dumps(arguments, sort_keys=True)}")
            if name == "memory_list":
                result = memory_store.list()
                memory_metadata["memory_entry_ids_available"] = result["memory_entry_ids_available"]
                memory_metadata["memory_index"] = result["memory_index"]
            elif name == "memory_get":
                entry = memory_store.get(str(arguments.get("id", "")))
                result = {
                    "id": entry.id,
                    "type": entry.type,
                    "title": entry.title,
                    "content": entry.content,
                } if entry is not None else {"error": "memory id not found"}
                if entry is not None:
                    if entry.id not in memory_metadata["memory_entry_ids_retrieved"]:
                        memory_metadata["memory_entry_ids_retrieved"].append(entry.id)
                    memory_metadata["retrieved_context"].append(entry.content)
            else:
                result = {"error": f"unknown tool {name}"}
            memory_metadata["memory_tool_calls"].append(
                {
                    "step_name": step_name,
                    "tool_name": name,
                    "arguments": arguments,
                    "result": result,
                }
            )
            if name == "memory_get":
                memory_metadata["retrieval_queries"].append(str(arguments.get("id", "")))
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id") or f"call_{uuid.uuid4().hex[:8]}",
                    "output": json.dumps(result),
                }
            )
        return outputs

    def _parse_tool_arguments(self, raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _maybe_summarize_transcript(
        self,
        *,
        transcript: Transcript,
        prompt_response_log: list[dict[str, Any]],
        memory_metadata: dict[str, Any],
    ) -> dict[str, int]:
        estimated_tokens = transcript.estimate_context_tokens()
        if estimated_tokens < self.config.loop_summary_trigger_tokens:
            return {"model_calls": 0, "input_tokens": 0, "output_tokens": 0}
        items_to_summarize = transcript.items_for_summarization(keep_last_messages=self.config.loop_summary_keep_messages)
        if not items_to_summarize:
            return {"model_calls": 0, "input_tokens": 0, "output_tokens": 0}
        print(
            "[loop] compacting history "
            f"({estimated_tokens} estimated tokens >= {self.config.loop_summary_trigger_tokens})"
        )
        response = self.model_client.generate_items(
            instructions=SUMMARY_PROMPT,
            input_items=items_to_summarize,
            step_name="context_summary",
        )
        prompt_response_log.append(
            {
                "step_name": "context_summary",
                "instructions": SUMMARY_PROMPT,
                "input_items": items_to_summarize,
                "output_items": response["output_items"],
                "response": response["text"],
                "input_tokens": response["input_tokens"],
                "output_tokens": response["output_tokens"],
                "duration_ms": response["duration_ms"],
                "response_id": response["response_id"],
            }
        )
        transcript.apply_summary(
            summary_text=response["text"],
            keep_last_messages=self.config.loop_summary_keep_messages,
        )
        memory_metadata["rolling_summary_triggered"] = True
        print("[loop] compaction complete")
        return {
            "model_calls": 1,
            "input_tokens": response["input_tokens"],
            "output_tokens": response["output_tokens"],
        }
