from __future__ import annotations

from typing import Any


def blank_memory_metadata(*, uses_memory: bool) -> dict[str, Any]:
    return {
        "uses_memory": uses_memory,
        "memory_entry_ids_available": [],
        "memory_entry_ids_retrieved": [],
        "retrieval_queries": [],
        "retrieved_context": [],
        "memory_index": "",
        "memory_tool_calls": [],
        "rolling_summary_triggered": False,
    }
