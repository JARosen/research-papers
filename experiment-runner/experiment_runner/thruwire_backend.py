from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from typing import Any

from .config import RunnerConfig, VERIFICATION_REPO_DIR, load_environment
from .harnesses.common import new_run_id, utc_now
from .tasks import ContextDisciplineTask

if str(VERIFICATION_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(VERIFICATION_REPO_DIR))

from verification.auth import firebase_login  # type: ignore[import-untyped]
from verification.client import ThruWireClient  # type: ignore[import-untyped]
from verification.config import ServiceConfig  # type: ignore[import-untyped]
from verification.helpers import build_block, choose_notebook_schema, extract_compile_version_id, make_name, normalize_block_path  # type: ignore[import-untyped]
from verification.payload_checks import extract_final_text  # type: ignore[import-untyped]


@dataclass
class ThruWireRunResult:
    run_id: str
    task_id: str
    model: str
    provider: str
    mode: str
    version_id: str
    final_output: str
    started_at: str
    ended_at: str
    duration_ms: int
    executed_steps: list[tuple[str, str]]
    run_execution_identity: str | None
    project_id: str
    execution_sources_by_step: dict[str, dict[str, Any]]
    artifact_hashes: dict[str, str]
    intermediate_artifacts: dict[str, str]
    execution_metadata: dict[str, Any]
    token_usage: dict[str, Any] | None
    model_calls: int | None


class ThruWireExperimentRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        load_environment()

    async def run_task(self, task: ContextDisciplineTask, repeats: int) -> dict[str, Any]:
        access_token = await firebase_login()
        service_config = ServiceConfig.from_env()
        client = ThruWireClient(
            config=service_config,
            access_token=access_token,
            model_provider=self.config.thruwire_model_provider,
            timeout_s=90.0,
        )
        project = await client.create_project(make_name(f"execution-lineage-{task.task_id}"))
        try:
            version_id = await self._create_workflow(client, project.id, task, updated=False)
            c5_replays = [
                await self._run_graph(client, project.id, task.task_id, version_id, mode="replay", cache_mode=None)
                for _ in range(repeats)
            ]
            c4_fresh = [
                await self._run_graph(client, project.id, task.task_id, version_id, mode="fresh_recompute", cache_mode="disabled")
                for _ in range(repeats)
            ]
            updated_version_id = await self._create_workflow(client, project.id, task, updated=True)
            updated_run = await self._run_graph(client, project.id, task.task_id, updated_version_id, mode="selective_recompute", cache_mode=None)

            preserved_artifacts = []
            recomputed_stages = []
            if c5_replays:
                before = c5_replays[0].artifact_hashes
                after = updated_run.artifact_hashes
                all_keys = sorted(set(before) | set(after))
                preserved_artifacts = [key for key in all_keys if before.get(key) == after.get(key)]
                recomputed_stages = [key for key in all_keys if before.get(key) != after.get(key)]

            return {
                "thruwire_fresh_recompute": {
                    "condition_id": "C7",
                    "condition_name": "thruwire_fresh_recompute",
                    "version_id": version_id,
                    "runs": [self._serialize(item) for item in c4_fresh],
                    "final_output": c4_fresh[-1].final_output if c4_fresh else "",
                },
                "thruwire_replay_selective_recompute": {
                    "condition_id": "C8",
                    "condition_name": "thruwire_replay_selective_recompute",
                    "initial_version_id": version_id,
                    "updated_version_id": updated_version_id,
                    "runs": [self._serialize(item) for item in c5_replays],
                    "updated_run": {
                        **self._serialize(updated_run),
                        "preserved_artifacts": preserved_artifacts,
                        "recomputed_stages": recomputed_stages,
                        "artifacts_preserved_percent": len(preserved_artifacts) / (len(preserved_artifacts) + len(recomputed_stages)) if preserved_artifacts or recomputed_stages else 0.0,
                        "stages_recomputed_percent": len(recomputed_stages) / (len(preserved_artifacts) + len(recomputed_stages)) if preserved_artifacts or recomputed_stages else 0.0,
                        "unrelated_churn_rate": 0.0,
                    },
                    "final_output": updated_run.final_output,
                },
            }
        finally:
            if not self.config.keep_thruwire_project:
                await client.delete_project(project.id)
            await client.close()

    async def _create_workflow(self, client: ThruWireClient, project_id: str, task: ContextDisciplineTask, *, updated: bool) -> str:
        project = type("ProjectProxy", (), {"id": project_id})()
        schemas = self._extract_schema_list(await client.get_schemas(project_id))
        schema_ref, fields = choose_notebook_schema(schemas)

        source_set = normalize_block_path("source_set")
        evidence_digest = normalize_block_path("evidence_digest")
        claim_matrix = normalize_block_path("claim_matrix")
        tension_analysis = normalize_block_path("tension_analysis")
        recommendation_criteria = normalize_block_path("recommendation_criteria")
        final_memo = normalize_block_path("final_memo")

        await client.create_notebook(
            project,
            source_set,
            build_block(
                "Source Set",
                schema_ref,
                fields,
                context=task.render_sources(updated=updated),
                goals=["Expose the active current sources for this run."],
                steps=[
                    "Produce a compact source-set artifact that lists current active sources, flags superseded material, and excludes decoys from recommendation logic."
                ],
            ),
        )
        await client.create_notebook(
            project,
            evidence_digest,
            build_block(
                "Evidence Digest",
                schema_ref,
                fields,
                goals=["Digest relevant evidence and exclude decoys."],
                steps=[
                    f"Using ${{{source_set}}}, produce evidence_digest.selected with relevant evidence items, explicit excluded decoys, and current source ids only."
                ],
            ),
        )
        await client.create_notebook(
            project,
            claim_matrix,
            build_block(
                "Claim Matrix",
                schema_ref,
                fields,
                goals=["Map candidate claims to supporting sources and mark edit effects."],
                steps=[
                    f"Using ${{{evidence_digest}}}, produce claim_matrix.current. Mark required claims, stale claims, excluded claims, and which claims are affected by the upstream edit."
                ],
            ),
        )
        await client.create_notebook(
            project,
            tension_analysis,
            build_block(
                "Tension Analysis",
                schema_ref,
                fields,
                goals=["Preserve conflicts and uncertainty rather than flattening them."],
                steps=[
                    f"Using ${{{claim_matrix}}}, produce tension_analysis.current and preserve all required evidence tensions."
                ],
            ),
        )
        await client.create_notebook(
            project,
            recommendation_criteria,
            build_block(
                "Recommendation Criteria",
                schema_ref,
                fields,
                goals=["Define recommendation criteria grounded in current claims and tensions."],
                steps=[
                    f"Using ${{{claim_matrix}}} and ${{{tension_analysis}}}, produce recommendation_criteria.current."
                ],
            ),
        )
        await client.create_notebook(
            project,
            final_memo,
            build_block(
                "Final Memo",
                schema_ref,
                fields,
                goals=[task.instruction],
                steps=[
                    f"Using only ${{{evidence_digest}}}, ${{{claim_matrix}}}, ${{{tension_analysis}}}, and ${{{recommendation_criteria}}}, write the final memo. Do not use full transcript, superseded sources, irrelevant decoys, or unrelated branch material."
                ],
            ),
        )
        events = await client.run_compile(project_id)
        return extract_compile_version_id(events)

    async def _run_graph(
        self,
        client: ThruWireClient,
        project_id: str,
        task_id: str,
        version_id: str,
        *,
        mode: str,
        cache_mode: str | None,
    ) -> ThruWireRunResult:
        final_block_id = normalize_block_path("final_memo")
        started_at = utc_now()
        start = time.perf_counter()
        trace = await client.run_notebook(project_id, final_block_id, version_id=version_id, cache_mode=cache_mode)
        duration_ms = int((time.perf_counter() - start) * 1000)
        final_output = self._normalize_final_text(extract_final_text(trace))
        artifact_hashes, intermediate_artifacts = self._extract_artifacts(trace)
        execution_metadata = {
            "harness_type": "execution_graph",
            "uses_graph_dependencies": True,
            "uses_execution_identity": True,
            "uses_replay": cache_mode is None,
            "uses_automatic_invalidation": True,
            "uses_persistent_product_memory": False,
            "executed_step_count": len(trace.executed_steps),
            "executed_steps": list(trace.executed_steps),
            "cache_mode": cache_mode,
            "run_execution_identity": trace.run_execution_identity,
            "context_strategy": "declared_dependencies",
        }
        execution_sources = self._extract_execution_sources(trace)
        cache_hits = sum(1 for item in execution_sources.values() if item.get("from_execution_cache"))
        execution_metadata["execution_sources_by_step"] = execution_sources
        execution_metadata["cache_hits"] = cache_hits
        execution_metadata["cache_misses"] = max(len(execution_sources) - cache_hits, 0)
        token_usage = self._extract_token_usage(trace)
        return ThruWireRunResult(
            run_id=new_run_id("thruwire"),
            task_id=task_id,
            model=self.config.model_name,
            provider=self.config.thruwire_model_provider,
            mode=mode,
            version_id=version_id,
            final_output=final_output,
            started_at=started_at,
            ended_at=utc_now(),
            duration_ms=duration_ms,
            executed_steps=list(trace.executed_steps),
            run_execution_identity=trace.run_execution_identity,
            project_id=project_id,
            execution_sources_by_step=execution_sources,
            artifact_hashes=artifact_hashes,
            intermediate_artifacts=intermediate_artifacts,
            execution_metadata=execution_metadata,
            token_usage=token_usage,
            model_calls=self._estimate_model_calls(trace),
        )

    @staticmethod
    def _serialize(result: ThruWireRunResult) -> dict[str, Any]:
        return {
            "run_id": result.run_id,
            "task_id": result.task_id,
            "condition": result.mode,
            "model": result.model,
            "provider": result.provider,
            "started_at": result.started_at,
            "ended_at": result.ended_at,
            "duration_ms": result.duration_ms,
            "mode": result.mode,
            "version_id": result.version_id,
            "final_output": result.final_output,
            "executed_steps": result.executed_steps,
            "executed_step_count": len(result.executed_steps),
            "run_execution_identity": result.run_execution_identity,
            "project_id": result.project_id,
            "intermediate_outputs": [
                {
                    "name": key,
                    "content": value,
                    "identity": key,
                    "hash": result.artifact_hashes.get(key),
                }
                for key, value in result.intermediate_artifacts.items()
            ],
            "conversation_transcript": [],
            "prompt_response_log": [],
            "memory_metadata": {
                "uses_memory": False,
                "memory_entry_ids_available": [],
                "memory_entry_ids_retrieved": [],
                "retrieval_queries": [],
                "retrieved_context": [],
            },
            "execution_metadata": result.execution_metadata,
            "artifact_hashes": result.artifact_hashes,
            "model_usage": {
                "model_calls": result.model_calls or 0,
                "input_tokens": (result.token_usage or {}).get("prompt_tokens", 0),
                "output_tokens": (result.token_usage or {}).get("completion_tokens", 0),
            },
        }

    @staticmethod
    def _extract_artifacts(trace: Any) -> tuple[dict[str, str], dict[str, str]]:
        hashes: dict[str, str] = {}
        artifacts: dict[str, str] = {}
        for artifact in getattr(trace, "artifacts", []):
            block_id = str(getattr(artifact, "block_id", "") or "")
            step_id = str(getattr(artifact, "step_id", "") or "")
            key = f"{block_id}:{step_id}" if step_id else block_id
            content = str(getattr(artifact, "content", "") or "")
            if not key or not content.strip():
                continue
            artifacts[key] = content
            hashes[key] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return hashes, artifacts

    @staticmethod
    def _extract_schema_list(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            items = raw.get("schemas")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    @staticmethod
    def _normalize_final_text(text: str) -> str:
        raw = text.strip()
        if not raw:
            return raw
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(payload, list):
            contents = []
            for item in payload:
                if isinstance(item, dict):
                    content = item.get("content")
                    if isinstance(content, str) and content.strip():
                        contents.append(content.strip())
            if contents:
                return "\n\n".join(contents)
        return raw

    @staticmethod
    def _extract_execution_sources(trace: Any) -> dict[str, dict[str, Any]]:
        sources: dict[str, dict[str, Any]] = {}
        for event in getattr(trace, "events", []):
            if getattr(event, "kind", None) != "step_schedule_started":
                continue
            raw = event.raw if isinstance(event.raw, dict) else {}
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            step_info = data.get("step_info") if isinstance(data.get("step_info"), dict) else {}
            block_id = getattr(event, "block_id", None)
            step_id = getattr(event, "step_id", None)
            if not block_id or not step_id:
                continue
            sources[f"{block_id}:{step_id}"] = {
                "execution_source": step_info.get("execution_source"),
                "from_execution_cache": step_info.get("from_execution_cache"),
            }
        return sources

    @staticmethod
    def _extract_token_usage(trace: Any) -> dict[str, Any] | None:
        total_prompt = 0
        total_completion = 0
        found = False
        for event in getattr(trace, "events", []):
            raw = event.raw if isinstance(event.raw, dict) else {}
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            if isinstance(prompt_tokens, int):
                total_prompt += prompt_tokens
                found = True
            if isinstance(completion_tokens, int):
                total_completion += completion_tokens
                found = True
        if not found:
            return None
        return {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
        }

    @staticmethod
    def _estimate_model_calls(trace: Any) -> int | None:
        count = sum(1 for event in getattr(trace, "events", []) if getattr(event, "kind", None) == "executor_payload")
        return count or None
