from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional

from .config import RunnerConfig, VERIFICATION_REPO_DIR, load_environment
from .tasks import ResearchTask

if str(VERIFICATION_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(VERIFICATION_REPO_DIR))

from verification.auth import firebase_login  # type: ignore[import-untyped]
from verification.client import ThruWireClient  # type: ignore[import-untyped]
from verification.config import ServiceConfig  # type: ignore[import-untyped]
from verification.helpers import build_block, choose_notebook_schema, extract_compile_version_id, make_name, normalize_block_path  # type: ignore[import-untyped]
from verification.payload_checks import extract_final_text  # type: ignore[import-untyped]


@dataclass
class ThruWireRunResult:
    mode: str
    version_id: str
    final_text: str
    duration_s: float
    executed_steps: list[tuple[str, str]]
    run_execution_identity: Optional[str]
    project_id: str
    execution_sources_by_step: dict[str, dict[str, Any]]


class ThruWireExperimentRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        load_environment()

    async def run_task(self, task: ResearchTask, repeats: int) -> dict[str, Any]:
        print("[thruwire] authenticating ...")
        access_token = await firebase_login()
        service_config = ServiceConfig.from_env()
        client = ThruWireClient(
            config=service_config,
            access_token=access_token,
            model_provider=self.config.thruwire_model_provider,
            timeout_s=90.0,
        )
        print("[thruwire] creating project ...")
        project = await client.create_project(make_name(f"paper-experiment-{task.task_id}"))
        try:
            print(f"[thruwire] created project {project.id}")
            print("[thruwire] creating initial workflow ...")
            version_id = await self._create_workflow(client, project.id, task)
            print(f"[thruwire] compiled initial workflow version {version_id}")
            replay_results = []
            for index in range(repeats):
                print(f"[thruwire] starting replay-enabled repeated run {index + 1}/{repeats}")
                result = await self._run_brief(client, project.id, version_id, mode="replay_enabled", cache_mode=None)
                replay_results.append(result)
                print(
                    f"[thruwire] completed replay-enabled repeated run {index + 1}/{repeats} "
                    f"in {result.duration_s:.2f}s with {len(result.executed_steps)} executed steps"
                )

            fresh_results = []
            for index in range(repeats):
                print(f"[thruwire] starting fresh-recompute repeated run {index + 1}/{repeats}")
                result = await self._run_brief(client, project.id, version_id, mode="fresh_recompute", cache_mode="disabled")
                fresh_results.append(result)
                print(
                    f"[thruwire] completed fresh-recompute repeated run {index + 1}/{repeats} "
                    f"in {result.duration_s:.2f}s with {len(result.executed_steps)} executed steps"
                )

            print("[thruwire] applying upstream source update ...")
            updated_version_id = await self._update_sources(client, project.id, task)
            print(f"[thruwire] compiled updated workflow version {updated_version_id}")
            print("[thruwire] starting updated run (replay-enabled)")
            updated_result = await self._run_brief(client, project.id, updated_version_id, mode="updated_replay_enabled", cache_mode=None)
            print(
                f"[thruwire] completed updated run in {updated_result.duration_s:.2f}s "
                f"with {len(updated_result.executed_steps)} executed steps"
            )

            return {
                "project_id": project.id,
                "initial_version_id": version_id,
                "updated_version_id": updated_version_id,
                "replay_repeats": [self._serialize_result(item) for item in replay_results],
                "fresh_repeats": [self._serialize_result(item) for item in fresh_results],
                "updated": self._serialize_result(updated_result),
            }
        finally:
            if not self.config.keep_thruwire_project:
                print(f"[thruwire] deleting project {project.id}")
                await client.delete_project(project.id)
            await client.close()

    async def _create_workflow(self, client: ThruWireClient, project_id: str, task: ResearchTask) -> str:
        project = type("ProjectProxy", (), {"id": project_id})()
        schemas = self._extract_schema_list(await client.get_schemas(project_id))
        schema_ref, fields = choose_notebook_schema(schemas)

        source_block_id = normalize_block_path("sources")
        framing_block_id = normalize_block_path("framing")
        analysis_block_id = normalize_block_path("analysis")
        brief_block_id = normalize_block_path("brief")

        framing_block = build_block(
            "Framing",
            schema_ref,
            fields,
            context=f"Topic: {task.topic}",
            goals=["Establish a stable evaluation framing and output outline for the brief."],
            steps=[
                "Produce a concise framing note that defines the output structure, evaluation lens, and section headings for the final brief."
            ],
        )
        source_block = build_block(
            "Sources",
            schema_ref,
            fields,
            context=task.source_packet(updated=False),
            goals=[f"Digest source materials for topic: {task.topic}"],
            steps=[
                "Review the source packet in context. Produce a structured digest with numbered evidence items, one per major point, and keep source references stable."
            ],
        )
        analysis_block = build_block(
            "Analysis",
            schema_ref,
            fields,
            goals=[f"Analyze the evidence for topic: {task.topic}"],
            steps=[
                f"Using ${{{source_block_id}}} and ${{{framing_block_id}}}, identify key claims, strongest supporting evidence, tensions, and open questions."
            ],
        )
        brief_block = build_block(
            "Brief",
            schema_ref,
            fields,
            goals=[task.instructions],
            steps=[
                f"Using ${{{analysis_block_id}}} and ${{{framing_block_id}}}, write a one-page brief with sections for overview, major claims, evidence, and unresolved questions."
            ],
        )

        await client.create_notebook(project, framing_block_id, framing_block)
        await client.create_notebook(project, source_block_id, source_block)
        await client.create_notebook(project, analysis_block_id, analysis_block)
        await client.create_notebook(project, brief_block_id, brief_block)

        events = await client.run_compile(project_id)
        return extract_compile_version_id(events)

    async def _update_sources(self, client: ThruWireClient, project_id: str, task: ResearchTask) -> str:
        project = type("ProjectProxy", (), {"id": project_id})()
        schemas = self._extract_schema_list(await client.get_schemas(project_id))
        schema_ref, fields = choose_notebook_schema(schemas)
        source_block_id = normalize_block_path("sources")
        source_block = build_block(
            "Sources",
            schema_ref,
            fields,
            context=task.source_packet(updated=True),
            goals=[f"Digest source materials for topic: {task.topic}"],
            steps=[
                "Review the revised source packet in context. Produce a structured digest with numbered evidence items, one per major point, and keep source references stable."
            ],
        )
        await client.update_notebook(project, source_block_id, source_block)
        events = await client.run_compile(project_id)
        return extract_compile_version_id(events)

    async def _run_brief(
        self,
        client: ThruWireClient,
        project_id: str,
        version_id: str,
        *,
        mode: str,
        cache_mode: Optional[str],
    ) -> ThruWireRunResult:
        brief_block_id = normalize_block_path("brief")
        start = time.perf_counter()
        trace = await client.run_notebook(project_id, brief_block_id, version_id=version_id, cache_mode=cache_mode)
        duration_s = time.perf_counter() - start
        final_text = self._normalize_final_text(extract_final_text(trace))
        return ThruWireRunResult(
            mode=mode,
            version_id=version_id,
            final_text=final_text,
            duration_s=duration_s,
            executed_steps=list(trace.executed_steps),
            run_execution_identity=trace.run_execution_identity,
            project_id=project_id,
            execution_sources_by_step=self._extract_execution_sources(trace),
        )

    @staticmethod
    def _serialize_result(result: ThruWireRunResult) -> dict[str, Any]:
        return {
            "mode": result.mode,
            "version_id": result.version_id,
            "final_text": result.final_text,
            "duration_s": result.duration_s,
            "executed_steps": result.executed_steps,
            "executed_step_count": len(result.executed_steps),
            "run_execution_identity": result.run_execution_identity,
            "project_id": result.project_id,
            "execution_sources_by_step": result.execution_sources_by_step,
        }

    @staticmethod
    def _extract_schema_list(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            schemas = raw.get("schemas")
            if isinstance(schemas, list):
                return [item for item in schemas if isinstance(item, dict)]
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
            key = f"{block_id}:{step_id}"
            sources[key] = {
                "execution_source": step_info.get("execution_source"),
                "from_execution_cache": step_info.get("from_execution_cache"),
            }
        return sources
