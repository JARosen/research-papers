from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from experiment_runner.harnesses.common import utc_now


@dataclass
class TranscriptEntry:
    item: dict[str, Any]
    step_name: str
    timestamp: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    carry_forward: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "role": self.item.get("role"),
            "type": self.item.get("type"),
            "step_name": self.step_name,
            "timestamp": self.timestamp,
            "model": self.model,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration_ms": self.duration_ms,
            "carry_forward": self.carry_forward,
        }


@dataclass
class Transcript:
    entries: list[TranscriptEntry] = field(default_factory=list)
    rolling_summary: str = ""
    summary_boundary: int = 0
    omitted_history: list[dict[str, Any]] = field(default_factory=list)

    def add_message(
        self,
        *,
        role: str,
        content: str,
        step_name: str,
        model: str,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
    ) -> None:
        item: dict[str, Any] = {
            "type": "message",
            "role": role,
            "content": content,
        }
        if role == "assistant":
            item["phase"] = "commentary"
        self.add_item(
            item=item,
            step_name=step_name,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
        )

    def add(
        self,
        *,
        role: str,
        content: str,
        step_name: str,
        model: str,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
    ) -> None:
        self.add_message(
            role=role,
            content=content,
            step_name=step_name,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
        )

    def add_item(
        self,
        *,
        item: dict[str, Any],
        step_name: str,
        model: str,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
        carry_forward: bool = True,
    ) -> None:
        self.entries.append(
            TranscriptEntry(
                item=item,
                step_name=step_name,
                timestamp=utc_now(),
                model=model,
                provider=provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                carry_forward=carry_forward,
            )
        )

    def add_output_items(
        self,
        *,
        items: list[dict[str, Any]],
        step_name: str,
        model: str,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
        carry_forward: bool = True,
    ) -> None:
        for item in items:
            self.add_item(
                item=item,
                step_name=step_name,
                model=model,
                provider=provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                carry_forward=carry_forward,
            )

    def current_input_items(self, *, current_step_name: str | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if self.rolling_summary:
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": self.rolling_summary,
                    "phase": "commentary",
                }
            )
        for entry in self.entries[self.summary_boundary :]:
            if not entry.carry_forward:
                if current_step_name is None or entry.step_name != current_step_name:
                    continue
            items.append(entry.item)
        return items

    def estimate_context_tokens(self) -> int:
        total_chars = len(self.rolling_summary)
        for entry in self.entries[self.summary_boundary :]:
            total_chars += len(json.dumps(entry.item, sort_keys=True))
        return max(1, total_chars // 4)

    def items_for_summarization(self, *, keep_last_messages: int) -> list[dict[str, Any]]:
        boundary = max(self.summary_boundary, len(self.entries) - keep_last_messages)
        items: list[dict[str, Any]] = []
        for entry in self.entries[self.summary_boundary : boundary]:
            if entry.item.get("type") in {"function_call", "function_call_output"}:
                continue
            items.append(entry.item)
        return items

    def apply_summary(self, *, summary_text: str, keep_last_messages: int) -> None:
        boundary = max(self.summary_boundary, len(self.entries) - keep_last_messages)
        if boundary <= self.summary_boundary:
            return
        self.omitted_history.append(
            {
                "from_entry_index": self.summary_boundary,
                "to_entry_index": boundary,
                "summary_chars": len(summary_text),
            }
        )
        self.rolling_summary = summary_text.strip()
        self.summary_boundary = boundary

    def to_list(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]
