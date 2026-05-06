from __future__ import annotations

import csv
import json
import os
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from experiment_runner.config import EXPERIMENTS_DIR, REPO_DIR, RunnerConfig
from experiment_runner.harnesses.base import HarnessRunResult
from experiment_runner.harnesses.common import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
    sha256_text,
    utc_now,
)
from experiment_runner.harnesses.loop_centric.runner import LoopCentricHarnessRunner
from experiment_runner.harnesses.simple_dag.runner import SimpleDAGHarnessRunner
from experiment_runner.tasks import ContextDisciplineTask, load_task


PAPER_CONDITIONS = [
    "loop_centric_fresh",
    "loop_real_world_final_update",
    "loop_real_world_with_edit_event",
    "loop_real_world_with_notes",
    "loop_real_world_with_memory",
    "simple_dag_fresh_recompute",
    "simple_dag_replay_selective_recompute",
]

PAPER_DIAGNOSTIC_CONDITIONS = [
    "loop_centric_fresh",
    "loop_real_world_with_notes",
    "simple_dag_fresh_recompute",
    "simple_dag_replay_selective_recompute",
]

DEFAULT_TASK_FILES = {
    "local_supersession_update": EXPERIMENTS_DIR / "tasks" / "local_supersession_update" / "task.json",
    "multi_edit_interaction_update": EXPERIMENTS_DIR / "tasks" / "multi_edit_interaction_update" / "task.json",
    "multi_round_cumulative_update": EXPERIMENTS_DIR / "tasks" / "multi_round_cumulative_update" / "manifest.json",
}

JUDGE_PROMPT_PATH = EXPERIMENTS_DIR / "prompts" / "judge_prompt.md"
JUDGE_SCHEMA_PATH = EXPERIMENTS_DIR / "schemas" / "judge_output.schema.json"


def _judge_create_with_retries(client: OpenAI, config: RunnerConfig, kwargs: dict[str, Any]) -> Any:
    request_kwargs = dict(kwargs)
    stripped_seed = False
    last_error: Exception | None = None
    for attempt in range(config.openai_max_retries + 1):
        try:
            return client.responses.create(**request_kwargs)
        except TypeError:
            if stripped_seed:
                raise
            request_kwargs.pop("seed", None)
            stripped_seed = True
        except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError) as err:
            last_error = err
            if attempt >= config.openai_max_retries:
                break
            sleep_s = (config.openai_retry_base_delay_ms / 1000.0) * (2**attempt)
            print(f"judge request failed ({err.__class__.__name__}); retrying in {sleep_s:.1f}s ...")
            import time

            time.sleep(sleep_s)
    if last_error is not None:
        raise last_error
    raise RuntimeError("OpenAI judge request failed without an exception")


@dataclass
class RoundSpec:
    round_id: str
    task_file: Path


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _sanitize_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned or "item"


def _render_prompt_entry(entry: dict[str, Any], index: int) -> dict[str, Any]:
    step_name = str(entry.get("step_name") or f"call_{index}")
    instructions = str(entry.get("instructions") or "")
    input_items = entry.get("input_items", [])
    rendered_markdown = "\n\n".join(
        [
            f"# Prompt Call {index:02d}",
            f"Step: {step_name}",
            "## Instructions",
            instructions,
            "## Input Items",
            "```json",
            json.dumps(input_items, indent=2),
            "```",
        ]
    ).strip()
    return {
        "call_index": index,
        "call_name": f"{index:02d}_{_sanitize_name(step_name)}",
        "rendered_markdown": rendered_markdown,
        "sha256": sha256_text(rendered_markdown),
    }


def _attach_rendered_prompts_to_run(run_payload: dict[str, Any]) -> None:
    prompt_log = run_payload.get("prompt_response_log")
    if not isinstance(prompt_log, list):
        return
    run_payload["rendered_prompts"] = [
        _render_prompt_entry(entry, index + 1)
        for index, entry in enumerate(prompt_log)
        if isinstance(entry, dict)
    ]


def _write_rendered_prompts(root: Path, run_payload: dict[str, Any]) -> None:
    _attach_rendered_prompts_to_run(run_payload)
    for item in run_payload.get("rendered_prompts", []):
        if not isinstance(item, dict):
            continue
        filename = f"{item.get('call_name', 'prompt')}.md"
        target = root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(item.get("rendered_markdown", "")))


def _condition_payload(name: str, run: dict[str, Any], **extras: Any) -> dict[str, Any]:
    payload = {
        "condition_id": name,
        "condition_name": name,
        "run": run,
        "final_output": run.get("final_output", ""),
        "execution_metadata": run.get("execution_metadata", {}),
        "model_usage": run.get("model_usage", {}),
    }
    payload.update(extras)
    return payload


def _latest_run(condition_payload: dict[str, Any]) -> dict[str, Any]:
    return dict(condition_payload.get("run", {}))


def _load_cumulative_manifest(path: Path) -> list[RoundSpec]:
    payload = json.loads(path.read_text())
    task_dir = path.parent
    return [
        RoundSpec(round_id=str(item["round_id"]), task_file=task_dir / str(item["task_file"]))
        for item in payload.get("rounds", [])
    ]


def _git_commit_hash() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _load_judge_client() -> OpenAI | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key)


def _normalize_schema(schema: Any, *, path: tuple[str, ...] = ()) -> Any:
    if isinstance(schema, dict):
        normalized = {key: _normalize_schema(value, path=path + (key,)) for key, value in schema.items()}
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
        return [_normalize_schema(item, path=path) for item in schema]
    return schema


def _build_judge_prompt(prompt_template: str, bundle: dict[str, Any]) -> str:
    return (
        f"{prompt_template.strip()}\n\n"
        "Return a JSON object with a top-level `systems` array.\n"
        "Each system must preserve the bundle's `anonymous_id`.\n"
        "Use null instead of guessing when the bundle lacks enough evidence for a field.\n\n"
        "Judge bundle:\n"
        f"{json.dumps(bundle, indent=2)}"
    )


def _validate_judge_output(bundle: dict[str, Any], payload: dict[str, Any]) -> None:
    expected_ids = {item["anonymous_id"] for item in bundle.get("systems", [])}
    actual_ids = {item.get("anonymous_id") for item in payload.get("systems", [])}
    if expected_ids != actual_ids:
        raise RuntimeError(
            "Judge output anonymous_id set does not match bundle systems. "
            f"expected={sorted(expected_ids)} actual={sorted(actual_ids)}"
        )


def _build_judge_bundle(task: ContextDisciplineTask, condition_payloads: dict[str, dict[str, Any]], seed: int) -> tuple[dict[str, Any], dict[str, str]]:
    sources = json.loads((task.task_dir / "sources.json").read_text())
    edits = json.loads((task.task_dir / "edits.json").read_text())
    ground_truth = json.loads((task.task_dir / "ground_truth.json").read_text())
    labels = [f"System {letter}" for letter in "ABCDEFG"]
    random.Random(seed).shuffle(labels)
    mapping = {labels[index]: condition_id for index, condition_id in enumerate(sorted(condition_payloads.keys()))}
    systems = []
    for anonymous_id, condition_id in mapping.items():
        payload = _latest_run(condition_payloads[condition_id])
        transcript = payload.get("conversation_transcript", [])
        systems.append(
            {
                "anonymous_id": anonymous_id,
                "condition_id": "hidden",
                "final_output": payload.get("final_output"),
                "intermediate_outputs": payload.get("intermediate_outputs", []),
                "conversation_transcript_excerpt": transcript[:6],
                "execution_metadata": payload.get("execution_metadata"),
                "memory_metadata": payload.get("memory_metadata"),
            }
        )
    bundle = {
        "bundle_id": f"judge_traceability_{task.task_id}",
        "judge_mode": "traceability",
        "task_id": task.task_id,
        "task_instruction": task.instruction,
        "source_bundle": sources["sources"],
        "controlled_hazards": {
            "irrelevant_decoys": [item["id"] for item in sources["sources"] if item.get("kind") == "irrelevant_decoy"],
            "superseded": [item["id"] for item in sources["sources"] if item.get("status") == "superseded"],
            "conflicting_pairs": ground_truth["sources"].get("conflicting_pairs", []),
        },
        "upstream_edit": edits["edits"],
        "systems": systems,
    }
    return bundle, mapping


def _run_judge(
    client: OpenAI,
    config: RunnerConfig,
    bundle: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    prompt_template = JUDGE_PROMPT_PATH.read_text()
    schema = _normalize_schema(json.loads(JUDGE_SCHEMA_PATH.read_text()))
    prompt = _build_judge_prompt(prompt_template, bundle)
    response = _judge_create_with_retries(
        client,
        config,
        {
            "model": model,
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
    _validate_judge_output(bundle, payload)
    return payload


def _summary_scores_by_condition(judge_output: dict[str, Any], mapping: dict[str, str]) -> dict[str, dict[str, Any]]:
    by_condition: dict[str, dict[str, Any]] = {}
    for system in judge_output.get("systems", []):
        anonymous_id = system.get("anonymous_id")
        condition_id = mapping.get(str(anonymous_id))
        if condition_id is None:
            continue
        by_condition[condition_id] = {
            "claims": system.get("claims", []),
            **dict(system.get("summary_scores", {})),
        }
    return by_condition


def _claim_overlap_score(claim: dict[str, Any], expected: dict[str, Any]) -> float:
    from difflib import SequenceMatcher

    left = " ".join(str(claim.get("claim_text") or "").split()).lower()
    right = " ".join(str(expected.get("text") or "").split()).lower()
    score = SequenceMatcher(None, left, right).ratio()
    supporting_sources = set(str(item) for item in claim.get("supporting_sources", []))
    expected_sources = set(str(item) for item in expected.get("supported_by", []))
    if supporting_sources and expected_sources:
        union = len(supporting_sources | expected_sources)
        if union:
            score += len(supporting_sources & expected_sources) / union
    return score


def _match_claim_id(claim: dict[str, Any], expected_claims: list[dict[str, Any]]) -> str:
    explicit = claim.get("claim_id")
    if explicit:
        return str(explicit)
    if not expected_claims:
        return str(claim.get("claim_text") or "")
    ranked = sorted(
        ((expected, _claim_overlap_score(claim, expected)) for expected in expected_claims),
        key=lambda item: item[1],
        reverse=True,
    )
    best_expected, best_score = ranked[0]
    if best_score < 0.35:
        return str(claim.get("claim_text") or "")
    return str(best_expected.get("id"))


def _changed_claim_metrics(claims: list[dict[str, Any]], ground_truth: dict[str, Any]) -> tuple[float, float, float]:
    affected = {
        str(item.get("id"))
        for item in ground_truth.get("expected_claims", [])
        if bool(item.get("affected_by_edit"))
    }
    unaffected = {
        str(item.get("id"))
        for item in ground_truth.get("expected_claims", [])
        if not bool(item.get("affected_by_edit"))
    }
    expected_claims = list(ground_truth.get("expected_claims", []))
    predicted_changed = {
        _match_claim_id(item, expected_claims)
        for item in claims
        if item.get("affected_by_upstream_edit") is True or item.get("changed_appropriately") is True
    }
    regressed = {
        _match_claim_id(item, expected_claims)
        for item in claims
        if item.get("unaffected_regression") is True
    }
    true_positive = len(predicted_changed & affected)
    precision = true_positive / len(predicted_changed) if predicted_changed else 0.0
    recall = true_positive / len(affected) if affected else 1.0
    regression = len(regressed & unaffected) / len(unaffected) if unaffected else 0.0
    return precision, recall, regression


def _pairwise_scores(values: list[str]) -> tuple[float, float]:
    from difflib import SequenceMatcher

    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return 1.0, 1.0
    similarities: list[float] = []
    overlaps: list[float] = []
    normalized = [" ".join((value or "").split()).strip().lower() for value in values]
    sentence_sets = [
        {
            sentence.strip()
            for sentence in text.replace(";", ".").split(".")
            if sentence.strip()
        }
        for text in normalized
    ]
    for index in range(len(values)):
        for other in range(index + 1, len(values)):
            similarities.append(SequenceMatcher(None, normalized[index], normalized[other]).ratio())
            union = sentence_sets[index] | sentence_sets[other]
            overlap = len(sentence_sets[index] & sentence_sets[other]) / len(union) if union else 1.0
            overlaps.append(overlap)
    return mean(similarities), mean(overlaps)


def _extract_preserved_recomputed(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    all_keys = sorted(set(before) | set(after))
    preserved = [key for key in all_keys if before.get(key) == after.get(key)]
    recomputed = [key for key in all_keys if before.get(key) != after.get(key)]
    return preserved, recomputed


def _run_single_update_repeat(
    task: ContextDisciplineTask,
    config: RunnerConfig,
    *,
    repeat_index: int,
    condition_ids: list[str],
) -> dict[str, dict[str, Any]]:
    loop_runner = LoopCentricHarnessRunner(config)
    dag_runner = SimpleDAGHarnessRunner(config)
    selected = set(condition_ids)

    loop_prior = None
    if selected & {
        "loop_centric_fresh",
        "loop_real_world_final_update",
        "loop_real_world_with_edit_event",
        "loop_real_world_with_notes",
    }:
        loop_prior = loop_runner.run_fresh(task, repeats=1, updated=False)
    loop_fresh_current = loop_runner.run_fresh(task, repeats=1, updated=True).payload["runs"][0] if "loop_centric_fresh" in selected else None
    loop_update = loop_runner.run_update(task, loop_prior, task.update_edits) if "loop_real_world_final_update" in selected and loop_prior is not None else None
    loop_edit_event = (
        loop_runner.run_update(task, loop_prior, task.update_edits, include_edit_event=True)
        if "loop_real_world_with_edit_event" in selected and loop_prior is not None
        else None
    )
    loop_notes = (
        loop_runner.run_update(task, loop_prior, task.update_edits, include_intermediates=True)
        if "loop_real_world_with_notes" in selected and loop_prior is not None
        else None
    )
    memory_prior = loop_runner.run_fresh_with_procedural_memory(task, repeats=1, updated=False) if "loop_real_world_with_memory" in selected else None
    loop_memory = (
        loop_runner.run_update(
            task,
            memory_prior,
            task.update_edits,
            include_intermediates=True,
            use_procedural_memory=True,
        )
        if memory_prior is not None
        else None
    )

    dag_initial = dag_replay_probe = dag_update = dag_fresh = None
    if selected & {"simple_dag_fresh_recompute", "simple_dag_replay_selective_recompute"}:
        dag_initial = dag_runner._run_graph(task, updated=False, allow_replay=True)
        if "simple_dag_replay_selective_recompute" in selected:
            dag_replay_probe = dag_runner._run_graph(task, updated=False, allow_replay=True)
            dag_update = dag_runner._run_graph(task, updated=True, allow_replay=True)
            preserved, recomputed = _extract_preserved_recomputed(dag_initial.get("artifact_hashes", {}), dag_update.get("artifact_hashes", {}))
            dag_update["preserved_artifacts"] = preserved
            dag_update["recomputed_stages"] = recomputed
            total = len(preserved) + len(recomputed)
            dag_update["artifacts_preserved_percent"] = len(preserved) / total if total else 0.0
            dag_update["stages_recomputed_percent"] = len(recomputed) / total if total else 0.0
            dag_update["unrelated_churn_rate"] = 0.0
        if "simple_dag_fresh_recompute" in selected:
            dag_fresh = dag_runner._run_graph(task, updated=True, allow_replay=False)

    payloads: dict[str, dict[str, Any]] = {}
    if loop_fresh_current is not None and loop_prior is not None:
        payloads["loop_centric_fresh"] = _condition_payload("loop_centric_fresh", loop_fresh_current, prior_run=loop_prior.payload["runs"][0])
    if loop_update is not None and loop_prior is not None:
        payloads["loop_real_world_final_update"] = _condition_payload("loop_real_world_final_update", loop_update.payload, prior_run=loop_prior.payload["runs"][0])
    if loop_edit_event is not None and loop_prior is not None:
        payloads["loop_real_world_with_edit_event"] = _condition_payload("loop_real_world_with_edit_event", loop_edit_event.payload, prior_run=loop_prior.payload["runs"][0])
    if loop_notes is not None and loop_prior is not None:
        payloads["loop_real_world_with_notes"] = _condition_payload("loop_real_world_with_notes", loop_notes.payload, prior_run=loop_prior.payload["runs"][0])
    if loop_memory is not None and memory_prior is not None:
        payloads["loop_real_world_with_memory"] = _condition_payload("loop_real_world_with_memory", loop_memory.payload, prior_run=memory_prior.payload["runs"][0])
    if dag_fresh is not None:
        payloads["simple_dag_fresh_recompute"] = _condition_payload("simple_dag_fresh_recompute", dag_fresh)
    if dag_update is not None and dag_initial is not None and dag_replay_probe is not None:
        payloads["simple_dag_replay_selective_recompute"] = _condition_payload(
            "simple_dag_replay_selective_recompute",
            dag_update,
            initial_run=dag_initial,
            replay_probe=dag_replay_probe,
            exact_artifact_hash_replay_match=dag_initial.get("artifact_hashes") == dag_replay_probe.get("artifact_hashes"),
            exact_final_output_replay_match=dag_initial.get("final_output") == dag_replay_probe.get("final_output"),
        )
    return payloads


def _run_cumulative_repeat(
    rounds: list[RoundSpec],
    config: RunnerConfig,
    *,
    condition_ids: list[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    loop_runner = LoopCentricHarnessRunner(config)
    dag_runner = SimpleDAGHarnessRunner(config)
    prior_runs: dict[str, HarnessRunResult] = {}
    outputs_by_round: dict[str, dict[str, dict[str, Any]]] = {}
    selected = set(condition_ids)

    for index, round_spec in enumerate(rounds):
        task = load_task(round_spec.task_file)
        if index == 0:
            if selected & {"loop_centric_fresh", "loop_real_world_final_update", "loop_real_world_with_edit_event", "loop_real_world_with_notes"}:
                prior_runs["loop_real_world_final_update"] = loop_runner.run_fresh(task, repeats=1, updated=False)
                prior_runs["loop_real_world_with_edit_event"] = prior_runs["loop_real_world_final_update"]
                prior_runs["loop_real_world_with_notes"] = prior_runs["loop_real_world_final_update"]
            if "loop_real_world_with_memory" in selected:
                prior_runs["loop_real_world_with_memory"] = loop_runner.run_fresh_with_procedural_memory(task, repeats=1, updated=False)

        dag_initial = dag_runner._run_graph(task, updated=False, allow_replay=True) if selected & {"simple_dag_fresh_recompute", "simple_dag_replay_selective_recompute"} else None

        loop_fresh_current = loop_runner.run_fresh(task, repeats=1, updated=True).payload["runs"][0] if "loop_centric_fresh" in selected else None
        loop_update = (
            loop_runner.run_update(task, prior_runs["loop_real_world_final_update"], task.update_edits)
            if "loop_real_world_final_update" in selected
            else None
        )
        loop_edit_event = (
            loop_runner.run_update(
                task,
                prior_runs["loop_real_world_with_edit_event"],
                task.update_edits,
                include_edit_event=True,
            )
            if "loop_real_world_with_edit_event" in selected
            else None
        )
        loop_notes = (
            loop_runner.run_update(
                task,
                prior_runs["loop_real_world_with_notes"],
                task.update_edits,
                include_intermediates=True,
            )
            if "loop_real_world_with_notes" in selected
            else None
        )
        loop_memory = (
            loop_runner.run_update(
                task,
                prior_runs["loop_real_world_with_memory"],
                task.update_edits,
                include_intermediates=True,
                use_procedural_memory=True,
            )
            if "loop_real_world_with_memory" in selected
            else None
        )

        dag_replay_probe = dag_update = dag_fresh = None
        if "simple_dag_replay_selective_recompute" in selected and dag_initial is not None:
            dag_replay_probe = dag_runner._run_graph(task, updated=False, allow_replay=True)
            dag_update = dag_runner._run_graph(task, updated=True, allow_replay=True)
            preserved, recomputed = _extract_preserved_recomputed(dag_initial.get("artifact_hashes", {}), dag_update.get("artifact_hashes", {}))
            total = len(preserved) + len(recomputed)
            dag_update["preserved_artifacts"] = preserved
            dag_update["recomputed_stages"] = recomputed
            dag_update["artifacts_preserved_percent"] = len(preserved) / total if total else 0.0
            dag_update["stages_recomputed_percent"] = len(recomputed) / total if total else 0.0
            dag_update["unrelated_churn_rate"] = 0.0
        if "simple_dag_fresh_recompute" in selected:
            dag_fresh = dag_runner._run_graph(task, updated=True, allow_replay=False)

        round_payloads: dict[str, dict[str, Any]] = {}
        if loop_fresh_current is not None:
            round_payloads["loop_centric_fresh"] = _condition_payload("loop_centric_fresh", loop_fresh_current)
        if loop_update is not None:
            round_payloads["loop_real_world_final_update"] = _condition_payload("loop_real_world_final_update", loop_update.payload)
        if loop_edit_event is not None:
            round_payloads["loop_real_world_with_edit_event"] = _condition_payload("loop_real_world_with_edit_event", loop_edit_event.payload)
        if loop_notes is not None:
            round_payloads["loop_real_world_with_notes"] = _condition_payload("loop_real_world_with_notes", loop_notes.payload)
        if loop_memory is not None:
            round_payloads["loop_real_world_with_memory"] = _condition_payload("loop_real_world_with_memory", loop_memory.payload)
        if dag_fresh is not None:
            round_payloads["simple_dag_fresh_recompute"] = _condition_payload("simple_dag_fresh_recompute", dag_fresh)
        if dag_update is not None and dag_initial is not None and dag_replay_probe is not None:
            round_payloads["simple_dag_replay_selective_recompute"] = _condition_payload(
                "simple_dag_replay_selective_recompute",
                dag_update,
                initial_run=dag_initial,
                replay_probe=dag_replay_probe,
                exact_artifact_hash_replay_match=dag_initial.get("artifact_hashes") == dag_replay_probe.get("artifact_hashes"),
                exact_final_output_replay_match=dag_initial.get("final_output") == dag_replay_probe.get("final_output"),
            )
        outputs_by_round[round_spec.round_id] = round_payloads

        if loop_update is not None:
            prior_runs["loop_real_world_final_update"] = HarnessRunResult(loop_update.payload)
        if loop_edit_event is not None:
            prior_runs["loop_real_world_with_edit_event"] = HarnessRunResult(loop_edit_event.payload)
        if loop_notes is not None:
            prior_runs["loop_real_world_with_notes"] = HarnessRunResult(loop_notes.payload)
        if loop_memory is not None:
            prior_runs["loop_real_world_with_memory"] = HarnessRunResult(loop_memory.payload)

    return outputs_by_round


def _store_condition_artifacts(
    root: Path,
    task_id: str,
    round_id: str | None,
    repeat_id: str,
    condition_id: str,
    condition_payload: dict[str, Any],
) -> None:
    path_parts = [task_id]
    if round_id:
        path_parts.append(round_id)
    base = root.joinpath(*path_parts, condition_id, repeat_id)
    run_payload = _latest_run(condition_payload)
    _write_json(base / "final_run.json", run_payload)
    final_output = str(run_payload.get("final_output", ""))
    (base / "final_output.md").parent.mkdir(parents=True, exist_ok=True)
    (base / "final_output.md").write_text(final_output)
    _write_rendered_prompts(root.parent / "rendered_prompts" / Path(*path_parts) / condition_id / repeat_id / "final_run", run_payload)
    for extra_name in ["prior_run", "initial_run", "replay_probe"]:
        extra = condition_payload.get(extra_name)
        if not isinstance(extra, dict):
            continue
        _write_json(base / f"{extra_name}.json", extra)
        _write_rendered_prompts(root.parent / "rendered_prompts" / Path(*path_parts) / condition_id / repeat_id / extra_name, extra)


def _numeric(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _condition_row(
    *,
    task: ContextDisciplineTask,
    condition_id: str,
    condition_payload: dict[str, Any],
    repeat_id: str,
    round_id: str | None,
    judge_scores: dict[str, Any] | None,
) -> dict[str, Any]:
    run_payload = _latest_run(condition_payload)
    execution_metadata = dict(run_payload.get("execution_metadata", {}))
    model_usage = dict(run_payload.get("model_usage", {}))
    row: dict[str, Any] = {
        "task_id": task.task_id,
        "condition_id": condition_id,
        "repeat_id": repeat_id,
        "round_id": round_id,
        "final_output_hash": sha256_text(str(run_payload.get("final_output", ""))),
        "model_call_count": model_usage.get("model_calls"),
        "input_tokens": model_usage.get("input_tokens"),
        "output_tokens": model_usage.get("output_tokens"),
        "wall_clock_ms": run_payload.get("duration_ms"),
        "manual_context_reconstruction_actions": execution_metadata.get("manual_context_reconstruction_actions"),
        "stages_recomputed": len(run_payload.get("recomputed_stages", [])),
        "stages_recomputed_percent": run_payload.get("stages_recomputed_percent"),
        "artifacts_preserved": len(run_payload.get("preserved_artifacts", [])),
        "artifacts_preserved_percent": run_payload.get("artifacts_preserved_percent"),
        "unrelated_churn_rate": run_payload.get("unrelated_churn_rate"),
    }
    if condition_id == "simple_dag_replay_selective_recompute":
        row["exact_artifact_hash_match_rate"] = 1.0 if condition_payload.get("exact_artifact_hash_replay_match") else 0.0
        row["final_output_exact_match_rate"] = 1.0 if condition_payload.get("exact_final_output_replay_match") else 0.0
        row["distinct_replay_outputs"] = 1
    if judge_scores:
        claims = list(judge_scores.get("claims", []))
        precision, recall, regression = _changed_claim_metrics(claims, task.ground_truth)
        row.update(
            {
                "changed_claim_precision": precision,
                "changed_claim_recall": recall,
                "unaffected_claim_regression_rate": regression,
                "unsupported_claim_rate": judge_scores.get("unsupported_claim_rate"),
                "stale_context_usage_rate": judge_scores.get("stale_context_usage_rate"),
                "evidence_recall_score": judge_scores.get("evidence_recall_score"),
                "required_tension_preservation_score": judge_scores.get("tension_preservation_score"),
                "dependency_compliance_score": judge_scores.get("dependency_compliance_score"),
                "claim_level_traceability_score": judge_scores.get("traceability_score"),
                "context_discipline_score": judge_scores.get("context_discipline_score"),
                "output_faithfulness_score": judge_scores.get("output_faithfulness_score"),
                "current_state_precision_score": judge_scores.get("current_state_precision"),
                "stale_structure_retention_score": judge_scores.get("stale_structure_retention_score"),
                "cross_artifact_consistency_score": judge_scores.get("cross_artifact_consistency_score"),
                "recommendation_delta_correctness_score": judge_scores.get("recommendation_delta_correctness_score"),
                "implementation_delta_completeness_score": judge_scores.get("implementation_delta_completeness_score"),
                "interaction_effect_coverage_score": judge_scores.get("interaction_effect_coverage_score"),
            }
        )
    return row


def _aggregate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metric_keys = [
        "output_faithfulness_score",
        "stale_context_usage_rate",
        "unsupported_claim_rate",
        "current_state_precision_score",
        "changed_claim_precision",
        "changed_claim_recall",
        "unaffected_claim_regression_rate",
        "unrelated_churn_rate",
        "input_tokens",
        "model_call_count",
        "wall_clock_ms",
        "stages_recomputed",
        "artifacts_preserved",
        "manual_context_reconstruction_actions",
        "exact_artifact_hash_match_rate",
        "final_output_exact_match_rate",
        "evidence_recall_score",
        "claim_level_traceability_score",
        "context_discipline_score",
        "stale_structure_retention_score",
        "cross_artifact_consistency_score",
        "recommendation_delta_correctness_score",
        "implementation_delta_completeness_score",
        "interaction_effect_coverage_score",
    ]
    groups: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("task_id")), str(row.get("condition_id")), row.get("round_id"))
        groups.setdefault(key, []).append(row)
    summaries: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        task_id, condition_id, round_id = key
        summary: dict[str, Any] = {
            "task_id": task_id,
            "condition_id": condition_id,
            "round_id": round_id,
            "n": len(items),
        }
        for metric_key in metric_keys:
            values = [_numeric(item.get(metric_key)) for item in items]
            numbers = [value for value in values if value is not None]
            if not numbers:
                continue
            summary[f"{metric_key}_mean"] = mean(numbers)
            summary[f"{metric_key}_std"] = stdev(numbers) if len(numbers) > 1 else 0.0
            summary[f"{metric_key}_min"] = min(numbers)
            summary[f"{metric_key}_max"] = max(numbers)
        final_hashes = [str(item.get("final_output_hash", "")) for item in items if item.get("final_output_hash")]
        if final_hashes:
            summary["distinct_replay_outputs"] = len(set(final_hashes))
        texts = [str(item.get("_final_output_text", "")) for item in items if item.get("_final_output_text")]
        if texts:
            surface_similarity, claim_overlap = _pairwise_scores(texts)
            summary["fresh_run_surface_similarity_mean"] = surface_similarity
            summary["fresh_run_semantic_claim_overlap_mean"] = claim_overlap
        summaries.append(summary)
    return summaries


def _write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    all_keys: list[str] = []
    key_set: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in key_set:
                key_set.add(key)
                all_keys.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=all_keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_paper_minimal(
    *,
    config: RunnerConfig,
    output_dir: Path,
    repeats: int,
    judge_repeats: int,
    task_ids: list[str] | None = None,
    condition_ids: list[str] | None = None,
    mode: str = "full",
) -> dict[str, Any]:
    selected_task_ids = task_ids or list(DEFAULT_TASK_FILES.keys())
    selected_conditions = condition_ids or list(PAPER_CONDITIONS)
    client = _load_judge_client()
    if judge_repeats > 0 and client is None:
        raise RuntimeError(
            "OPENAI_API_KEY is required for paper-minimal judging. "
            "Set --judge-repeats 0 to run generation only."
        )
    commit_hash = _git_commit_hash()
    metadata = {
        "schema_version": "execution_lineage.paper_package.v1",
        "created_at": utc_now(),
        "repo_commit": commit_hash,
        "model_provider": config.model_provider,
        "model_name": config.model_name,
        "model_temperature": config.model_temperature,
        "model_seed": config.model_seed,
        "judge_model": config.openai_eval_model,
        "judge_repeats": judge_repeats,
        "judge_enabled": judge_repeats > 0,
        "repeats": repeats,
        "conditions": selected_conditions,
        "mode": mode,
        "tasks": selected_task_ids,
    }
    _write_json(output_dir / "run_metadata.json", metadata)

    all_rows: list[dict[str, Any]] = []
    run_index: dict[str, Any] = {"tasks": {}}

    for task_id in selected_task_ids:
        task_path = DEFAULT_TASK_FILES[task_id]
        if task_path.name == "manifest.json":
            rounds = _load_cumulative_manifest(task_path)
            task_runs: dict[str, Any] = {}
            for repeat_index in range(1, repeats + 1):
                repeat_id = f"repeat_{repeat_index:02d}"
                round_outputs = _run_cumulative_repeat(rounds, config, condition_ids=selected_conditions)
                task_runs[repeat_id] = {}
                for round_spec in rounds:
                    task = load_task(round_spec.task_file)
                    condition_payloads = round_outputs[round_spec.round_id]
                    for condition_id, condition_payload in condition_payloads.items():
                        _store_condition_artifacts(output_dir / "outputs", task.task_id, round_spec.round_id, repeat_id, condition_id, condition_payload)
                    judge_scores_by_condition: dict[str, dict[str, Any]] = {}
                    if client is not None and judge_repeats > 0:
                        judge_bundle, mapping = _build_judge_bundle(task, condition_payloads, seed=repeat_index)
                        judge_input_dir = output_dir / "judge_inputs" / task.task_id / round_spec.round_id / repeat_id
                        judge_output_dir = output_dir / "judge_outputs" / task.task_id / round_spec.round_id / repeat_id
                        _write_json(judge_input_dir / "judge_bundle_traceability.json", judge_bundle)
                        _write_json(judge_input_dir / "system_mapping_traceability.json", mapping)
                        raw_outputs: list[dict[str, Any]] = []
                        for judge_index in range(1, judge_repeats + 1):
                            judge_output = _run_judge(client, config, judge_bundle, model=config.openai_eval_model)
                            raw_outputs.append(judge_output)
                            _write_json(judge_output_dir / f"judge_output_{judge_index:02d}.json", judge_output)
                        if raw_outputs:
                            judge_scores_by_condition = _summary_scores_by_condition(raw_outputs[-1], mapping)
                    task_runs[repeat_id][round_spec.round_id] = condition_payloads
                    for condition_id, condition_payload in condition_payloads.items():
                        row = _condition_row(
                            task=task,
                            condition_id=condition_id,
                            condition_payload=condition_payload,
                            repeat_id=repeat_id,
                            round_id=round_spec.round_id,
                            judge_scores=judge_scores_by_condition.get(condition_id),
                        )
                        row["_final_output_text"] = str(_latest_run(condition_payload).get("final_output", ""))
                        all_rows.append(row)
            run_index["tasks"][task_id] = task_runs
        else:
            task = load_task(task_path)
            task_runs = {}
            for repeat_index in range(1, repeats + 1):
                repeat_id = f"repeat_{repeat_index:02d}"
                condition_payloads = _run_single_update_repeat(task, config, repeat_index=repeat_index, condition_ids=selected_conditions)
                for condition_id, condition_payload in condition_payloads.items():
                    _store_condition_artifacts(output_dir / "outputs", task.task_id, None, repeat_id, condition_id, condition_payload)
                judge_scores_by_condition: dict[str, dict[str, Any]] = {}
                if client is not None and judge_repeats > 0:
                    judge_bundle, mapping = _build_judge_bundle(task, condition_payloads, seed=repeat_index)
                    judge_input_dir = output_dir / "judge_inputs" / task.task_id / repeat_id
                    judge_output_dir = output_dir / "judge_outputs" / task.task_id / repeat_id
                    _write_json(judge_input_dir / "judge_bundle_traceability.json", judge_bundle)
                    _write_json(judge_input_dir / "system_mapping_traceability.json", mapping)
                    raw_outputs: list[dict[str, Any]] = []
                    for judge_index in range(1, judge_repeats + 1):
                        judge_output = _run_judge(client, config, judge_bundle, model=config.openai_eval_model)
                        raw_outputs.append(judge_output)
                        _write_json(judge_output_dir / f"judge_output_{judge_index:02d}.json", judge_output)
                    if raw_outputs:
                        judge_scores_by_condition = _summary_scores_by_condition(raw_outputs[-1], mapping)
                task_runs[repeat_id] = condition_payloads
                for condition_id, condition_payload in condition_payloads.items():
                    row = _condition_row(
                        task=task,
                        condition_id=condition_id,
                        condition_payload=condition_payload,
                        repeat_id=repeat_id,
                        round_id=None,
                        judge_scores=judge_scores_by_condition.get(condition_id),
                    )
                    row["_final_output_text"] = str(_latest_run(condition_payload).get("final_output", ""))
                    all_rows.append(row)
            run_index["tasks"][task_id] = task_runs

    _write_json(output_dir / "paper_results.json", run_index)
    summary_rows = _aggregate_rows(all_rows)
    for row in all_rows:
        row.pop("_final_output_text", None)
    _write_json(output_dir / "summary_by_task_condition.json", summary_rows)
    _write_summary_csv(output_dir / "summary_by_task_condition.csv", summary_rows)
    _write_json(output_dir / "rows_by_task_condition_repeat.json", all_rows)
    return {
        "run_metadata": metadata,
        "summary_by_task_condition": summary_rows,
        "row_count": len(all_rows),
    }
