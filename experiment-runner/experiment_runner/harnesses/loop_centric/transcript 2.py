from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from experiment_runner.harnesses.common import utc_now


@dataclass
class TranscriptEntry:
    role: str
    content: str
    step_name: str
    timestamp: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "step_name": self.step_name,
            "timestamp": self.timestamp,
            "model": self.model,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration_ms": self.duration_ms,
        }


@dataclass
class Transcript:
    entries: list[TranscriptEntry] = field(default_factory=list)
    omitted_history: list[dict[str, Any]] = field(default_factory=list)

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
        self.entries.append(
            TranscriptEntry(
                role=role,
                content=content,
                step_name=step_name,
                timestamp=utc_now(),
                model=model,
                provider=provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
            )
        )

    def render_full_transcript(self) -> str:
        parts: list[str] = []
        for item in self.entries:
            parts.append(f"[{item.step_name}] {item.role}\n{item.content}")
        if self.omitted_history:
            parts.append(f"[history_omissions]\n{self.omitted_history}")
        return "\n\n".join(parts).strip()

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.entries]
