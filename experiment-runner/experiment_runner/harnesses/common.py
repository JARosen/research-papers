from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from experiment_runner.config import RunnerConfig, load_environment

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def new_run_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class OpenAITextModelClient:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        load_environment()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not found in environment or experiment-runner/.env")
        if OpenAI is None:
            raise RuntimeError("openai package is not installed in the active environment.")
        self.client = OpenAI(api_key=api_key)

    def generate(self, *, prompt: str, step_name: str) -> dict[str, Any]:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.config.model_name,
            "input": prompt,
        }
        if self.config.model_seed is not None:
            kwargs["seed"] = self.config.model_seed
        try:
            response = self.client.responses.create(**kwargs)
        except TypeError:
            kwargs.pop("seed", None)
            response = self.client.responses.create(**kwargs)
        duration_ms = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        output_text = getattr(response, "output_text", "") or ""
        return {
            "step_name": step_name,
            "text": output_text.strip(),
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
