from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_STAGE_SOURCE_IDS: dict[str, tuple[str, ...]] = {
    "utilization_context": ("S1",),
    "reimbursement_context": ("S2_old", "S2_current"),
    "operations_context": ("S3", "S6", "S7"),
    "access_cost_context": ("S4", "S5"),
}


@dataclass(frozen=True)
class SourceExcerpt:
    id: str
    title: str
    kind: str
    status: str
    active_in_initial: bool
    active_after_edit: bool
    excerpt: str


@dataclass(frozen=True)
class WorkflowStage:
    id: str
    label: str
    purpose: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class UpstreamEdit:
    edit_id: str
    description: str
    old_source_id: str | None
    new_source_id: str | None
    expected_affected_claim_ids: tuple[str, ...]
    expected_unaffected_claim_ids: tuple[str, ...]
    expected_recomputed_stages: tuple[str, ...]
    edit_type: str
    target_stage: str | None
    human_readable_event: str | None


@dataclass(frozen=True)
class ContextDisciplineTask:
    task_id: str
    task_family: str
    title: str
    instruction: str
    requested_output_format: dict[str, Any]
    workflow_stages: tuple[WorkflowStage, ...]
    sources: tuple[SourceExcerpt, ...]
    ground_truth: dict[str, Any]
    edits: tuple[UpstreamEdit, ...]
    manual_intermediates: dict[str, Any]
    initial_stage_source_ids: dict[str, tuple[str, ...]]
    updated_stage_source_ids: dict[str, tuple[str, ...]]
    update_user_note: str | None
    updated_stage_overrides: dict[str, str]
    memory_file: Path | None
    task_dir: Path

    @property
    def active_sources_initial(self) -> list[SourceExcerpt]:
        return [source for source in self.sources if source.active_in_initial]

    @property
    def active_sources_updated(self) -> list[SourceExcerpt]:
        return [source for source in self.sources if source.active_after_edit]

    @property
    def primary_edit(self) -> UpstreamEdit:
        return self.edits[0]

    @property
    def update_edits(self) -> tuple[UpstreamEdit, ...]:
        return self.edits

    def render_sources(self, *, updated: bool) -> str:
        selected = self.active_sources_updated if updated else self.active_sources_initial
        return self._render_source_list(selected)

    def stage_ids(self) -> tuple[str, ...]:
        return tuple(stage.id for stage in self.workflow_stages)

    def source_ids_for_stage(self, *, updated: bool, stage_name: str) -> tuple[str, ...]:
        stage_map = self.updated_stage_source_ids if updated else self.initial_stage_source_ids
        if stage_name in stage_map:
            return stage_map[stage_name]
        fallback_ids = DEFAULT_STAGE_SOURCE_IDS.get(stage_name)
        if fallback_ids is None:
            return ()
        return fallback_ids

    def stage_is_source_backed(self, stage_name: str) -> bool:
        stage_ids = set(self.stage_ids())
        stage = next((item for item in self.workflow_stages if item.id == stage_name), None)
        if stage is None:
            return False
        dependency_stage_ids = [dep for dep in stage.depends_on if dep in stage_ids]
        if dependency_stage_ids:
            return False
        return bool(self.source_ids_for_stage(updated=False, stage_name=stage_name) or self.source_ids_for_stage(updated=True, stage_name=stage_name))

    def source_items_for_stage(self, *, updated: bool, stage_name: str) -> list[SourceExcerpt]:
        selected = self.active_sources_updated if updated else self.active_sources_initial
        allowed_ids = self.source_ids_for_stage(updated=updated, stage_name=stage_name)
        if not allowed_ids:
            return []
        return [item for item in selected if any(self._source_matches_allowed_id(item.id, allowed_id) for allowed_id in allowed_ids)]

    def _render_source_list(self, selected: list[SourceExcerpt]) -> str:
        lines: list[str] = []
        for item in selected:
            lines.extend(
                [
                    f"[{item.id}] {item.title}",
                    f"Kind: {item.kind}",
                    f"Status: {item.status}",
                    item.excerpt.strip(),
                    "",
                ]
            )
        return "\n".join(lines).strip()

    @staticmethod
    def _source_matches_allowed_id(source_id: str, allowed_id: str) -> bool:
        return source_id == allowed_id or source_id.startswith(f"{allowed_id}_")

    def render_sources_for_stage(self, *, updated: bool, stage_name: str) -> str:
        stage_map = self.updated_stage_source_ids if updated else self.initial_stage_source_ids
        if stage_name not in stage_map and stage_name not in DEFAULT_STAGE_SOURCE_IDS:
            return self.render_sources(updated=updated)
        return self._render_source_list(self.source_items_for_stage(updated=updated, stage_name=stage_name))

    def render_manual_intermediates(self) -> str:
        lines: list[str] = []
        for name, value in self.manual_intermediates.items():
            lines.append(f"[{name}]")
            if isinstance(value, list):
                for item in value:
                    lines.append(f"- {item}")
            else:
                lines.append(str(value))
            lines.append("")
        return "\n".join(lines).strip()

    def loop_fresh_prompt(self) -> str:
        return (
            f"Task: {self.title}\n\n"
            f"Instruction:\n{self.instruction}\n\n"
            "Current source materials:\n"
            f"{self.render_sources(updated=False)}\n\n"
            "Requirements:\n"
            "- Use the current source materials for the memo.\n"
            "- Keep uncertainty and tradeoffs where the evidence is mixed.\n"
            "- Cite source IDs for evidence-based claims.\n"
            "- Return only the final memo.\n"
        )

    def loop_update_final_only_prompt(self, prior_final_output: str) -> str:
        return (
            "You previously drafted the memo below. Since then, the source materials have changed.\n\n"
            "Please produce an updated final memo that reflects the current evidence and follows the requested output format.\n\n"
            f"Prior memo:\n{prior_final_output}\n\n"
            "Current source materials:\n"
            f"{self.render_sources(updated=True)}\n\n"
            f"Task:\n{self.instruction}\n\n"
            f"Requested output format:\n{self.requested_output_format}\n"
        )

    def loop_update_with_intermediates_prompt(self, prior_final_output: str) -> str:
        return (
            "We are revising a policy memo using the latest available source materials.\n\n"
            f"Previous final memo:\n{prior_final_output}\n\n"
            "Prior working notes:\n"
            f"{self.render_manual_intermediates()}\n\n"
            "Current source materials:\n"
            f"{self.render_sources(updated=True)}\n\n"
            f"Task:\n{self.instruction}\n\n"
            f"Requested output format:\n{self.requested_output_format}\n"
        )

    def edit_event_lines(self) -> list[str]:
        lines: list[str] = []
        for edit in self.edits:
            if edit.human_readable_event:
                lines.append(f"- {edit.human_readable_event}")
                continue
            if edit.edit_type == "artifact_edit" and edit.target_stage:
                lines.append(f"- artifact `{edit.target_stage}` was updated")
                continue
            if edit.old_source_id and edit.new_source_id:
                lines.append(f"- source `{edit.old_source_id}` was replaced by source `{edit.new_source_id}`")
        return lines


def load_task(task_file: Path) -> ContextDisciplineTask:
    payload: dict[str, Any] = json.loads(task_file.read_text())
    task_dir = task_file.parent
    sources_payload = json.loads((task_dir / str(payload["source_file"])).read_text())
    ground_truth = json.loads((task_dir / str(payload["ground_truth_file"])).read_text())
    edits_payload = json.loads((task_dir / str(payload["edits_file"])).read_text())

    sources = tuple(
        SourceExcerpt(
            id=str(item["id"]),
            title=str(item["title"]),
            kind=str(item["kind"]),
            status=str(item["status"]),
            active_in_initial=bool(item["active_in_initial"]),
            active_after_edit=bool(item["active_after_edit"]),
            excerpt=str(item["excerpt"]),
        )
        for item in sources_payload["sources"]
    )
    stages = tuple(
        WorkflowStage(
            id=str(item["id"]),
            label=str(item["label"]),
            purpose=str(item["purpose"]),
            depends_on=tuple(item.get("depends_on", [])),
        )
        for item in payload["workflow_stages"]
    )
    edits = tuple(
        UpstreamEdit(
            edit_id=str(item["edit_id"]),
            description=str(item["description"]),
            old_source_id=str(item["old_source_id"]) if item.get("old_source_id") is not None else None,
            new_source_id=str(item["new_source_id"]) if item.get("new_source_id") is not None else None,
            expected_affected_claim_ids=tuple(item.get("expected_affected_claim_ids", [])),
            expected_unaffected_claim_ids=tuple(item.get("expected_unaffected_claim_ids", [])),
            expected_recomputed_stages=tuple(item.get("expected_recomputed_stages", [])),
            edit_type=str(item.get("type", "replace_source")),
            target_stage=str(item["target_stage"]) if item.get("target_stage") is not None else None,
            human_readable_event=str(item["human_readable_event"]) if item.get("human_readable_event") is not None else None,
        )
        for item in edits_payload["edits"]
    )
    initial_stage_source_ids = {
        str(stage_name): tuple(str(source_id) for source_id in source_ids)
        for stage_name, source_ids in dict(payload.get("initial_stage_source_ids", {})).items()
    }
    updated_stage_source_ids = {
        str(stage_name): tuple(str(source_id) for source_id in source_ids)
        for stage_name, source_ids in dict(payload.get("updated_stage_source_ids", {})).items()
    }
    updated_stage_overrides = {
        str(stage_name): str(content)
        for stage_name, content in dict(payload.get("updated_stage_overrides", {})).items()
    }
    return ContextDisciplineTask(
        task_id=str(payload["task_id"]),
        task_family=str(payload["task_family"]),
        title=str(payload["title"]),
        instruction=str(payload["instruction"]),
        requested_output_format=dict(payload["requested_output_format"]),
        workflow_stages=stages,
        sources=sources,
        ground_truth=ground_truth,
        edits=edits,
        manual_intermediates=dict(payload.get("manual_intermediates", {})),
        initial_stage_source_ids=initial_stage_source_ids,
        updated_stage_source_ids=updated_stage_source_ids,
        update_user_note=str(payload["update_user_note"]) if payload.get("update_user_note") is not None else None,
        updated_stage_overrides=updated_stage_overrides,
        memory_file=(task_dir / str(payload["memory_file"])) if payload.get("memory_file") else None,
        task_dir=task_dir,
    )
