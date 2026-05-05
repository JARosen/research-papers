from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceDocument:
    title: str
    url: str
    content: str


@dataclass(frozen=True)
class UpstreamEdit:
    replace_index: int
    source: SourceDocument


@dataclass(frozen=True)
class ResearchTask:
    task_id: str
    topic: str
    instructions: str
    sources: list[SourceDocument]
    upstream_edit: UpstreamEdit

    def rendered_sources(self, *, updated: bool = False) -> list[SourceDocument]:
        if not updated:
            return list(self.sources)
        sources = list(self.sources)
        sources[self.upstream_edit.replace_index] = self.upstream_edit.source
        return sources

    def source_packet(self, *, updated: bool = False) -> str:
        rendered = self.rendered_sources(updated=updated)
        lines: list[str] = []
        for index, source in enumerate(rendered, start=1):
            lines.extend(
                [
                    f"[Source {index}] {source.title}",
                    f"URL: {source.url}",
                    source.content.strip(),
                    "",
                ]
            )
        return "\n".join(lines).strip()

    def baseline_prompt(self) -> str:
        return (
            f"You are completing a short research workflow on the topic: {self.topic}.\n\n"
            f"Instructions:\n{self.instructions}\n\n"
            "Use the sources below. Work through source digestion, analysis, and synthesis internally, "
            "but return only the final brief.\n\n"
            f"{self.source_packet(updated=False)}"
        )

    def update_prompt(self) -> str:
        replacement = self.upstream_edit.source
        return (
            "Update the existing brief based on an upstream source revision.\n\n"
            f"Replace source {self.upstream_edit.replace_index + 1} with the following revised source:\n"
            f"Title: {replacement.title}\n"
            f"URL: {replacement.url}\n"
            f"Content: {replacement.content}\n\n"
            "Revise the analysis and final brief accordingly. Return only the updated final brief."
        )


def load_task(task_file: Path) -> ResearchTask:
    payload: dict[str, Any] = json.loads(task_file.read_text())
    sources = [
        SourceDocument(
            title=str(item["title"]),
            url=str(item["url"]),
            content=str(item["content"]),
        )
        for item in payload["sources"]
    ]
    edit_payload = payload["upstream_edit"]
    edit = UpstreamEdit(
        replace_index=int(edit_payload["replace_index"]),
        source=SourceDocument(
            title=str(edit_payload["source"]["title"]),
            url=str(edit_payload["source"]["url"]),
            content=str(edit_payload["source"]["content"]),
        ),
    )
    return ResearchTask(
        task_id=str(payload["task_id"]),
        topic=str(payload["topic"]),
        instructions=str(payload["instructions"]),
        sources=sources,
        upstream_edit=edit,
    )
