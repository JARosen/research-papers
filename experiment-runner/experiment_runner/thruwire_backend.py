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
    version_id: str
    final_text: str
    duration_s: float
    executed_steps: list[tuple[str, str]]
    run_execution_identity: Optional[str]
    project_id: str


class ThruWireExperimentRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        load_environment()

    async def run_task(self, task: ResearchTask, repeats: int) -> dict[str, Any]:
        access_token = await firebase_login()
        service_config = ServiceConfig.from_env()
        client = ThruWireClient(
            config=service_config,
            access_token=access_token,
            model_provider=self.config.thruwire_model_provider,
            timeout_s=90.0,
        )
        project = await client.create_project(make_name(f"paper-experiment-{task.task_id}"))
        try:
            version_id = await self._create_workflow(client, project.id, task)
            initial_results = []
            for _ in range(repeats):
                initial_results.append(await self._run_brief(client, project.id, version_id))

            updated_version_id = await self._update_sources(client, project.id, task)
            updated_result = await self._run_brief(client, project.id, updated_version_id)

            return {
                "project_id": project.id,
                "initial_version_id": version_id,
                "updated_version_id": updated_version_id,
                "repeats": [self._serialize_result(item) for item in initial_results],
                "updated": self._serialize_result(updated_result),
            }
        finally:
            if not self.config.keep_thruwire_project:
                await client.delete_project(project.id)
            await client.close()

    async def _create_workflow(self, client: ThruWireClient, project_id: str, task: ResearchTask) -> str:
        project = type("ProjectProxy", (), {"id": project_id})()
        schemas = self._extract_schema_list(await client.get_schemas(project_id))
        schema_ref, fields = choose_notebook_schema(schemas)

        source_block_id = normalize_block_path("sources")
        analysis_block_id = normalize_block_path("analysis")
        brief_block_id = normalize_block_path("brief")

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
                f"Using ${{{source_block_id}}}, identify key claims, strongest supporting evidence, tensions, and open questions."
            ],
        )
        brief_block = build_block(
            "Brief",
            schema_ref,
            fields,
            goals=[task.instructions],
            steps=[
                f"Using ${{{analysis_block_id}}}, write a one-page brief with sections for overview, major claims, evidence, and unresolved questions."
            ],
        )

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

    async def _run_brief(self, client: ThruWireClient, project_id: str, version_id: str) -> ThruWireRunResult:
        brief_block_id = normalize_block_path("brief")
        start = time.perf_counter()
        trace = await client.run_notebook(project_id, brief_block_id, version_id=version_id)
        duration_s = time.perf_counter() - start
        final_text = self._normalize_final_text(extract_final_text(trace))
        return ThruWireRunResult(
            version_id=version_id,
            final_text=final_text,
            duration_s=duration_s,
            executed_steps=list(trace.executed_steps),
            run_execution_identity=trace.run_execution_identity,
            project_id=project_id,
        )

    @staticmethod
    def _serialize_result(result: ThruWireRunResult) -> dict[str, Any]:
        return {
            "version_id": result.version_id,
            "final_text": result.final_text,
            "duration_s": result.duration_s,
            "executed_steps": result.executed_steps,
            "executed_step_count": len(result.executed_steps),
            "run_execution_identity": result.run_execution_identity,
            "project_id": result.project_id,
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
