from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STAGE_SOURCE_IDS: dict[str, tuple[str, ...]] = {
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
    old_source_id: str
    new_source_id: str
    expected_affected_claim_ids: tuple[str, ...]
    expected_unaffected_claim_ids: tuple[str, ...]


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

    def render_sources(self, *, updated: bool) -> str:
        selected = self.active_sources_updated if updated else self.active_sources_initial
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

    def render_sources_for_stage(self, *, updated: bool, stage_name: str) -> str:
        selected = self.active_sources_updated if updated else self.active_sources_initial
        allowed_ids = STAGE_SOURCE_IDS.get(stage_name)
        if allowed_ids is None:
            return self.render_sources(updated=updated)
        allowed = set(allowed_ids)
        lines: list[str] = []
        for item in selected:
            if item.id not in allowed:
                continue
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
            "Current source bundle:\n"
            f"{self.render_sources(updated=False)}\n\n"
            "Requirements:\n"
            "- Use only the current source bundle.\n"
            "- Ignore irrelevant but plausible adjacent material.\n"
            "- Preserve unresolved tensions and uncertainty.\n"
            "- Return only the final memo.\n"
        )

    def loop_update_final_only_prompt(self, prior_final_output: str) -> str:
        edit = self.primary_edit
        return (
            "Update an existing final memo after an upstream source revision.\n\n"
            f"Prior final memo:\n{prior_final_output}\n\n"
            f"Edit: {edit.description}\n\n"
            "Updated current source bundle:\n"
            f"{self.render_sources(updated=True)}\n\n"
            "Instructions:\n"
            "- Update only claims affected by the edit.\n"
            "- Preserve unaffected material where possible.\n"
            "- Do not use superseded or irrelevant context.\n"
            "- Return only the updated final memo.\n"
        )

    def loop_update_with_intermediates_prompt(self, prior_final_output: str) -> str:
        edit = self.primary_edit
        return (
            "Update an existing final memo after an upstream source revision.\n\n"
            f"Prior final memo:\n{prior_final_output}\n\n"
            "Manual intermediate notes and artifacts from the prior workflow:\n"
            f"{self.render_manual_intermediates()}\n\n"
            f"Edit: {edit.description}\n\n"
            "Updated current source bundle:\n"
            f"{self.render_sources(updated=True)}\n\n"
            "Instructions:\n"
            "- Treat the intermediate notes as manually bundled context rather than guaranteed truth.\n"
            "- Update only affected claims and preserve unaffected work.\n"
            "- Preserve required tensions and uncertainty.\n"
            "- Do not use superseded or irrelevant context.\n"
            "- Return only the updated final memo.\n"
        )


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
            old_source_id=str(item["old_source_id"]),
            new_source_id=str(item["new_source_id"]),
            expected_affected_claim_ids=tuple(item.get("expected_affected_claim_ids", [])),
            expected_unaffected_claim_ids=tuple(item.get("expected_unaffected_claim_ids", [])),
        )
        for item in edits_payload["edits"]
    )
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
        memory_file=(task_dir / str(payload["memory_file"])) if payload.get("memory_file") else None,
        task_dir=task_dir,
    )
