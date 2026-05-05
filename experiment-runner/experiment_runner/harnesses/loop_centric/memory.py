from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    type: str
    content: str


class TransparentMemoryStore:
    def __init__(self, memory_file: Path | None) -> None:
        self.memory_file = memory_file
        self.entries = self._load()

    def _load(self) -> list[MemoryEntry]:
        if self.memory_file is None or not self.memory_file.exists():
            return []
        payload = json.loads(self.memory_file.read_text())
        return [
            MemoryEntry(
                id=str(item["id"]),
                type=str(item["type"]),
                content=str(item["content"]),
            )
            for item in payload.get("memory_entries", [])
        ]

    def available_ids(self) -> list[str]:
        return [item.id for item in self.entries]

    def retrieve(self, *, query: str, include_prior_artifacts: bool) -> dict[str, Any]:
        lowered = query.lower()
        retrieved: list[MemoryEntry] = []
        for entry in self.entries:
            if entry.type == "workflow_recipe":
                retrieved.append(entry)
                continue
            if "superseded" in lowered and entry.type == "source_version_notes":
                retrieved.append(entry)
                continue
            if include_prior_artifacts and entry.type == "prior_artifact":
                retrieved.append(entry)
        return {
            "retrieval_query": query,
            "memory_entry_ids_retrieved": [item.id for item in retrieved],
            "retrieved_context": [item.content for item in retrieved],
        }
