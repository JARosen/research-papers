from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from experiment_runner.config import REPO_DIR, RunnerConfig, load_environment

from metric_lib import read_json, write_json

try:
    from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
    APIConnectionError = APITimeoutError = InternalServerError = RateLimitError = Exception  # type: ignore[misc,assignment]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the blinded judge over an anonymized judge bundle.")
    parser.add_argument("--bundle-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=REPO_DIR / "experiments" / "execution_lineage" / "prompts" / "judge_prompt.md",
    )
    parser.add_argument(
        "--schema-file",
        type=Path,
        default=REPO_DIR / "experiments" / "execution_lineage" / "schemas" / "judge_output.schema.json",
    )
    parser.add_argument("--model", default=os.getenv("OPENAI_EVAL_MODEL", "gpt-5-mini"))
    return parser.parse_args()


def load_client() -> OpenAI:
    load_environment()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not found in environment or experiment-runner/.env")
    if OpenAI is None:
        raise RuntimeError(
            "openai package is not installed in the active environment. "
            "Run `.venv/bin/pip install -e .` from experiment-runner or `.venv/bin/pip install openai`."
        )
    return OpenAI(api_key=api_key)


def create_with_retries(client: OpenAI, config: RunnerConfig, kwargs: dict[str, Any]) -> Any:
    request_kwargs = dict(kwargs)
    stripped_seed = False
    last_error: Exception | None = None
    max_retries = int(getattr(config, "openai_max_retries", 3))
    retry_base_delay_ms = int(getattr(config, "openai_retry_base_delay_ms", 1000))
    for attempt in range(max_retries + 1):
        try:
            return client.responses.create(**request_kwargs)
        except TypeError:
            if stripped_seed:
                raise
            request_kwargs.pop("seed", None)
            stripped_seed = True
        except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError) as err:
            last_error = err
            if attempt >= max_retries:
                break
            sleep_s = (retry_base_delay_ms / 1000.0) * (2**attempt)
            print(f"judge request failed ({err.__class__.__name__}); retrying in {sleep_s:.1f}s ...")
            time.sleep(sleep_s)
    if last_error is not None:
        raise last_error
    raise RuntimeError("OpenAI judge request failed without an exception")


def build_prompt(prompt_template: str, bundle: dict[str, Any]) -> str:
    return (
        f"{prompt_template.strip()}\n\n"
        "Return a JSON object with a top-level `systems` array.\n"
        "Each system must preserve the bundle's `anonymous_id`.\n"
        "Represent `required_tensions` as objects with at least `id`, `preserved`, and `explanation` when possible.\n"
        "Use null instead of guessing when the bundle lacks enough evidence for a field.\n\n"
        "Judge bundle:\n"
        f"{json.dumps(bundle, indent=2)}"
    )


def normalize_schema(schema: Any, *, path: tuple[str, ...] = ()) -> Any:
    if isinstance(schema, dict):
        normalized = {
            key: normalize_schema(value, path=path + (key,))
            for key, value in schema.items()
        }
        if normalized.get("type") == "object":
            normalized.setdefault("additionalProperties", False)
            properties = normalized.get("properties", {})
            if isinstance(properties, dict):
                normalized["required"] = list(properties.keys())
        if normalized.get("type") == "array" and "items" not in normalized:
            if path and path[-1] == "required_tensions":
                normalized["items"] = {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": ["string", "null"]},
                        "preserved": {"type": ["boolean", "null"]},
                        "explanation": {"type": ["string", "null"]},
                        "supporting_sources": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["id", "preserved", "explanation", "supporting_sources"],
                }
            else:
                normalized["items"] = {}
        return normalized
    if isinstance(schema, list):
        return [normalize_schema(item, path=path) for item in schema]
    return schema


def validate_output(bundle: dict[str, Any], payload: dict[str, Any]) -> None:
    expected_ids = {item["anonymous_id"] for item in bundle.get("systems", [])}
    actual_ids = {item.get("anonymous_id") for item in payload.get("systems", [])}
    if expected_ids != actual_ids:
        raise RuntimeError(
            "Judge output anonymous_id set does not match bundle systems. "
            f"expected={sorted(expected_ids)} actual={sorted(actual_ids)}"
        )


def main() -> None:
    args = parse_args()
    config = RunnerConfig.from_env()
    bundle = read_json(args.bundle_file)
    prompt_template = args.prompt_file.read_text()
    schema = normalize_schema(read_json(args.schema_file))
    client = load_client()
    prompt = build_prompt(prompt_template, bundle)

    response = create_with_retries(
        client,
        config,
        {
            "model": args.model,
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "judge_output",
                    "schema": schema,
                    "strict": True,
                }
            },
        },
    )
    output_text = getattr(response, "output_text", None)
    if not output_text:
        raise RuntimeError("Judge response did not include output_text.")

    payload = json.loads(output_text)
    validate_output(bundle, payload)
    write_json(args.output_file, payload)
    print(f"wrote {args.output_file}")


if __name__ == "__main__":
    main()
