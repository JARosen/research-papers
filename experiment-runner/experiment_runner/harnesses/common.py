from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from experiment_runner.config import RunnerConfig, load_environment

try:
    from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
    APIConnectionError = APITimeoutError = InternalServerError = RateLimitError = Exception  # type: ignore[misc,assignment]


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
        return self.generate_items(
            instructions="",
            input_items=[
                {
                    "type": "message",
                    "role": "user",
                    "content": prompt,
                }
            ],
            step_name=step_name,
        )

    def generate_items(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        step_name: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.config.model_name,
            "input": input_items,
        }
        if instructions:
            kwargs["instructions"] = instructions
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if self.config.model_seed is not None:
            kwargs["seed"] = self.config.model_seed
        response = self._create_with_retries(kwargs)
        duration_ms = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        output_text = getattr(response, "output_text", "") or ""
        response_dump = response.model_dump() if hasattr(response, "model_dump") else {}
        return {
            "step_name": step_name,
            "text": output_text.strip(),
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "output_items": list(response_dump.get("output", [])),
            "response_id": response_dump.get("id"),
        }

    def _create_with_retries(self, kwargs: dict[str, Any]) -> Any:
        request_kwargs = dict(kwargs)
        stripped_seed = False
        last_error: Exception | None = None
        for attempt in range(self.config.openai_max_retries + 1):
            try:
                return self.client.responses.create(**request_kwargs)
            except TypeError:
                if stripped_seed:
                    raise
                request_kwargs.pop("seed", None)
                stripped_seed = True
            except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError) as err:
                last_error = err
                if attempt >= self.config.openai_max_retries:
                    break
                sleep_s = (self.config.openai_retry_base_delay_ms / 1000.0) * (2**attempt)
                time.sleep(sleep_s)
        if last_error is not None:
            raise last_error
        raise RuntimeError("OpenAI request failed without an exception")
