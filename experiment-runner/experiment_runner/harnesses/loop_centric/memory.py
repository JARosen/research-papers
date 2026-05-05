from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    type: str
    title: str
    content: str
    path: Path | None = None


class TransparentMemoryStore:
    def __init__(self, memory_location: Path | None) -> None:
        self.memory_location = memory_location
        self.index_text = ""
        self.entries = self._load()
        self._entry_map = {item.id: item for item in self.entries}

    def _load(self) -> list[MemoryEntry]:
        if self.memory_location is None or not self.memory_location.exists():
            return []
        if self.memory_location.suffix == ".json":
            return self._load_legacy_json(self.memory_location)
        return self._load_markdown_memory(self.memory_location)

    def _load_legacy_json(self, memory_file: Path) -> list[MemoryEntry]:
        payload = json.loads(memory_file.read_text())
        entries = [
            MemoryEntry(
                id=str(item["id"]),
                type=str(item["type"]),
                title=str(item.get("title", item["id"])),
                content=str(item["content"]),
            )
            for item in payload.get("memory_entries", [])
        ]
        self.index_text = "\n".join(
            [
                "# Memory Index",
                *[
                    f"- `{entry.id}` ({entry.type}): {entry.title}"
                    for entry in entries
                ],
            ]
        ).strip()
        return entries

    def _load_markdown_memory(self, memory_location: Path) -> list[MemoryEntry]:
        index_path = memory_location if memory_location.name.lower() == "index.md" else memory_location / "INDEX.md"
        memory_dir = index_path.parent
        if index_path.exists():
            self.index_text = index_path.read_text().strip()
        entries: list[MemoryEntry] = []
        for entry_path in sorted(memory_dir.glob("*.md")):
            if entry_path.name.lower() == "index.md":
                continue
            content = entry_path.read_text().strip()
            title = entry_path.stem.replace("_", " ")
            first_line = content.splitlines()[0].strip() if content else ""
            if first_line.startswith("#"):
                title = first_line.lstrip("#").strip() or title
            entry_type = self._infer_type_from_id(entry_path.stem)
            entries.append(
                MemoryEntry(
                    id=entry_path.stem,
                    type=entry_type,
                    title=title,
                    content=content,
                    path=entry_path,
                )
            )
        if not self.index_text:
            self.index_text = "\n".join(
                [
                    "# Memory Index",
                    *[
                        f"- `{entry.id}` ({entry.type}): {entry.title}"
                        for entry in entries
                    ],
                ]
            ).strip()
        return entries

    def _infer_type_from_id(self, entry_id: str) -> str:
        if "workflow_recipe" in entry_id:
            return "workflow_recipe"
        if "source_version_notes" in entry_id:
            return "source_version_notes"
        if "summary" in entry_id:
            return "rolling_summary"
        return "prior_artifact"

    def available_ids(self) -> list[str]:
        return [item.id for item in self.entries]

    def list(self) -> dict[str, Any]:
        return {
            "memory_entry_ids_available": self.available_ids(),
            "memory_index": self.index_text,
        }

    def get(self, entry_id: str) -> MemoryEntry | None:
        return self._entry_map.get(entry_id)

    def get_many(self, entry_ids: list[str]) -> dict[str, Any]:
        retrieved = [self._entry_map[item_id] for item_id in entry_ids if item_id in self._entry_map]
        return {
            "memory_entry_ids_retrieved": [item.id for item in retrieved],
            "retrieved_context": [item.content for item in retrieved],
        }
