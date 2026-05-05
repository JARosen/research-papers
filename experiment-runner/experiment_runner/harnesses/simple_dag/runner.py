from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from experiment_runner.config import RunnerConfig
from experiment_runner.harnesses.base import HarnessRunResult
from experiment_runner.harnesses.common import OpenAITextModelClient, new_run_id, sha256_text, utc_now
from experiment_runner.harnesses.loop_centric.prompts import load_prompt
from experiment_runner.harnesses.loop_centric.transcript import Transcript
from experiment_runner.tasks import ContextDisciplineTask


STAGE_SPECS = [
    {"name": "source_set", "prompt": "loop_source_set.md", "deps": []},
    {"name": "evidence_digest", "prompt": "loop_evidence_digest.md", "deps": ["source_set"]},
    {"name": "claim_matrix", "prompt": "loop_claim_matrix.md", "deps": ["evidence_digest"]},
    {"name": "tension_analysis", "prompt": "loop_tension_analysis.md", "deps": ["claim_matrix"]},
    {"name": "recommendation_criteria", "prompt": "loop_recommendation_criteria.md", "deps": ["claim_matrix", "tension_analysis"]},
    {"name": "final_memo", "prompt": "loop_final_memo.md", "deps": ["evidence_digest", "claim_matrix", "tension_analysis", "recommendation_criteria"]},
]


@dataclass
class CachedArtifact:
    identity: str
    stage_name: str
    content: str
    hash_value: str
    prompt_text: str
    model_usage: dict[str, int]


class SimpleDAGHarnessRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.model_client = OpenAITextModelClient(config)
        self.cache: dict[str, CachedArtifact] = {}

    def run_all(self, task: ContextDisciplineTask, repeats: int) -> dict[str, HarnessRunResult]:
        replay_runs = [self._run_graph(task, updated=False, allow_replay=True) for _ in range(repeats)]
        fresh_runs = [self._run_graph(task, updated=False, allow_replay=False) for _ in range(repeats)]
        updated_run = self._run_graph(task, updated=True, allow_replay=True)

        preserved = []
        recomputed = []
        if replay_runs:
            before = replay_runs[0]["artifact_hashes"]
            after = updated_run["artifact_hashes"]
            all_keys = sorted(set(before) | set(after))
            preserved = [key for key in all_keys if before.get(key) == after.get(key)]
            recomputed = [key for key in all_keys if before.get(key) != after.get(key)]

        return {
            "simple_dag_fresh_recompute": HarnessRunResult(
                payload={
                    "condition_id": "C5",
                    "condition_name": "simple_dag_fresh_recompute",
                    "runs": fresh_runs,
                    "final_output": fresh_runs[-1]["final_output"] if fresh_runs else "",
                }
            ),
            "simple_dag_replay_selective_recompute": HarnessRunResult(
                payload={
                    "condition_id": "C6",
                    "condition_name": "simple_dag_replay_selective_recompute",
                    "runs": replay_runs,
                    "updated_run": {
                        **updated_run,
                        "preserved_artifacts": preserved,
                        "recomputed_stages": recomputed,
                        "artifacts_preserved_percent": len(preserved) / (len(preserved) + len(recomputed)) if preserved or recomputed else 0.0,
                        "stages_recomputed_percent": len(recomputed) / (len(preserved) + len(recomputed)) if preserved or recomputed else 0.0,
                        "unrelated_churn_rate": 0.0,
                    },
                    "final_output": updated_run["final_output"],
                }
            ),
        }

    def _run_graph(self, task: ContextDisciplineTask, *, updated: bool, allow_replay: bool) -> dict[str, Any]:
        transcript = Transcript()
        started_at = utc_now()
        run_start = time.perf_counter()
        source_text = task.render_sources(updated=updated)
        edit = task.primary_edit if updated else None

        artifacts_by_stage: dict[str, CachedArtifact] = {}
        execution_sources_by_step: dict[str, dict[str, Any]] = {}
        intermediate_outputs: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0
        model_calls = 0

        for spec in STAGE_SPECS:
            dependency_artifacts = [artifacts_by_stage[name] for name in spec["deps"]]
            stage_identity = self._stage_identity(
                task=task,
                stage_name=spec["name"],
                prompt_name=spec["prompt"],
                source_text=source_text,
                dependency_artifacts=dependency_artifacts,
                updated=updated,
            )
            if allow_replay and stage_identity in self.cache:
                artifact = self.cache[stage_identity]
                execution_sources_by_step[spec["name"]] = {
                    "execution_source": "replay",
                    "from_execution_cache": True,
                }
            else:
                user_prompt = self._build_stage_prompt(
                    task=task,
                    stage_name=spec["name"],
                    prompt_name=spec["prompt"],
                    source_text=source_text,
                    dependency_artifacts=dependency_artifacts,
                    edit=edit,
                )
                transcript.add(
                    role="user",
                    content=user_prompt,
                    step_name=spec["name"],
                    model=self.config.model_name,
                    provider=self.config.model_provider,
                )
                response = self.model_client.generate(prompt=user_prompt, step_name=spec["name"])
                model_calls += 1
                total_input_tokens += response["input_tokens"]
                total_output_tokens += response["output_tokens"]
                transcript.add(
                    role="assistant",
                    content=response["text"],
                    step_name=spec["name"],
                    model=self.config.model_name,
                    provider=self.config.model_provider,
                    input_tokens=response["input_tokens"],
                    output_tokens=response["output_tokens"],
                    duration_ms=response["duration_ms"],
                )
                artifact = CachedArtifact(
                    identity=stage_identity,
                    stage_name=spec["name"],
                    content=response["text"],
                    hash_value=sha256_text(response["text"]),
                    prompt_text=user_prompt,
                    model_usage={
                        "input_tokens": response["input_tokens"],
                        "output_tokens": response["output_tokens"],
                    },
                )
                if allow_replay:
                    self.cache[stage_identity] = artifact
                execution_sources_by_step[spec["name"]] = {
                    "execution_source": "fresh",
                    "from_execution_cache": False,
                }

            artifacts_by_stage[spec["name"]] = artifact
            intermediate_outputs.append(
                {
                    "name": spec["name"],
                    "content": artifact.content,
                    "identity": artifact.identity,
                    "hash": artifact.hash_value,
                }
            )

        artifact_hashes = {name: artifact.hash_value for name, artifact in artifacts_by_stage.items()}
        duration_ms = int((time.perf_counter() - run_start) * 1000)
        return {
            "run_id": new_run_id("simpledag"),
            "task_id": task.task_id,
            "condition": "simple_dag_replay" if allow_replay else "simple_dag_fresh",
            "model": self.config.model_name,
            "provider": self.config.model_provider,
            "started_at": started_at,
            "ended_at": utc_now(),
            "duration_ms": duration_ms,
            "final_output": artifacts_by_stage["final_memo"].content,
            "intermediate_outputs": intermediate_outputs,
            "conversation_transcript": transcript.to_list(),
            "prompt_response_log": transcript.to_list(),
            "memory_metadata": {
                "uses_memory": False,
                "memory_entry_ids_available": [],
                "memory_entry_ids_retrieved": [],
                "retrieval_queries": [],
                "retrieved_context": [],
            },
            "execution_metadata": {
                "harness_type": "simple_dag",
                "uses_graph_dependencies": True,
                "uses_execution_identity": True,
                "uses_replay": allow_replay,
                "uses_automatic_invalidation": True,
                "uses_persistent_product_memory": False,
                "context_strategy": "declared_dependencies",
                "manual_context_reconstruction_actions": 0,
                "execution_sources_by_step": execution_sources_by_step,
                "cache_hits": sum(1 for item in execution_sources_by_step.values() if item["from_execution_cache"]),
                "cache_misses": sum(1 for item in execution_sources_by_step.values() if not item["from_execution_cache"]),
            },
            "model_usage": {
                "model_calls": model_calls,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
            "artifact_hashes": artifact_hashes,
        }

    def _stage_identity(
        self,
        *,
        task: ContextDisciplineTask,
        stage_name: str,
        prompt_name: str,
        source_text: str,
        dependency_artifacts: list[CachedArtifact],
        updated: bool,
    ) -> str:
        payload = {
            "task_id": task.task_id,
            "stage_name": stage_name,
            "prompt_name": prompt_name,
            "prompt_text": load_prompt(prompt_name),
            "source_hash": sha256_text(source_text),
            "dependency_ids": [item.identity for item in dependency_artifacts],
            "updated": updated,
            "model": self.config.model_name,
            "provider": self.config.model_provider,
        }
        return sha256_text(json.dumps(payload, sort_keys=True))

    def _build_stage_prompt(
        self,
        *,
        task: ContextDisciplineTask,
        stage_name: str,
        prompt_name: str,
        source_text: str,
        dependency_artifacts: list[CachedArtifact],
        edit: Any,
    ) -> str:
        parts = [
            load_prompt(prompt_name),
            f"Task: {task.title}",
            f"Instruction:\n{task.instruction}",
        ]
        if stage_name == "source_set":
            parts.append(f"Current source bundle:\n{source_text}")
        else:
            rendered = []
            for item in dependency_artifacts:
                rendered.append(f"[{item.stage_name} | {item.identity}]\n{item.content}")
            parts.append("Declared dependency artifacts:\n" + "\n\n".join(rendered))
        if edit is not None:
            parts.append(
                f"Upstream edit:\n{edit.description}\nOld source id: {edit.old_source_id}\nNew source id: {edit.new_source_id}"
            )
        return "\n\n".join(parts).strip()
