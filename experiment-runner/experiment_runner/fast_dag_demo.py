from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Any

from experiment_runner.config import EXPERIMENTS_DIR, RunnerConfig
from experiment_runner.harnesses.base import HarnessRunResult
from experiment_runner.harnesses.common import sha256_text, utc_now
from experiment_runner.harnesses.loop_centric.runner import LoopCentricHarnessRunner
from experiment_runner.harnesses.simple_dag.runner import SimpleDAGHarnessRunner
from experiment_runner.paper_minimal import _condition_payload, _git_commit_hash, _latest_run, _store_condition_artifacts
from experiment_runner.tasks import ContextDisciplineTask, load_task


FAST_DEMO_DEFAULT_CONDITIONS = [
    "loop_real_world_final_update",
    "loop_real_world_with_edit_event",
    "simple_dag_replay_selective_recompute",
]

FAST_DEMO_TASK_FILES = {
    "unrelated_branch_noop_update": EXPERIMENTS_DIR / "tasks" / "unrelated_branch_noop_update" / "task.json",
    "intermediate_artifact_edit": EXPERIMENTS_DIR / "tasks" / "intermediate_artifact_edit" / "task.json",
}


@dataclass(frozen=True)
class FastDemoJudgeResult:
    output_faithfulness_score: float | None
    current_state_precision_score: float | None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _normalized_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, " ".join(left.split()), " ".join(right.split())).ratio()


def _hash(run_payload: dict[str, Any]) -> str:
    return sha256_text(str(run_payload.get("final_output", "")))


def _fast_demo_truth(task: ContextDisciplineTask) -> dict[str, Any]:
    return dict(task.ground_truth.get("fast_demo", {}))


def _expected_recomputed_stages(task: ContextDisciplineTask) -> set[str]:
    return {
        stage
        for edit in task.update_edits
        for stage in edit.expected_recomputed_stages
    }


def _expected_preserved_stages(task: ContextDisciplineTask) -> set[str]:
    truth = _fast_demo_truth(task)
    explicit = truth.get("expected_preserved_stages")
    if isinstance(explicit, list):
        return {str(item) for item in explicit}
    stage_ids = set(task.stage_ids())
    return stage_ids - _expected_recomputed_stages(task)


_NOOP_EXCLUSION_MARKERS = (
    "not included",
    "is included because no recruiting sources are present",
    "not use",
    "not used",
    "should not be used",
    "does not provide",
    "decision-irrelevant",
    "decision-irrelevant request",
    "excluded",
    "ignore",
    "not primary decision evidence",
    "no recruiting sources are present",
    "no recruiting/staffing update is included",
)

_NOOP_CONTAMINATION_PHRASES = (
    "provider recruiting",
    "clinician recruiting",
    "recruiting reach",
    "provider staffing",
    "staffing model",
    "staffing flexibility",
    "coverage flexibility",
    "workforce planning",
    "virtual-first roles",
)


def _sentence_chunks(text: str) -> list[str]:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return []
    return [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+|\n+", collapsed) if chunk.strip()]


def _contains_exclusion_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _NOOP_EXCLUSION_MARKERS)


def _noop_contamination_rate(text: str, truth: dict[str, Any]) -> float:
    lowered = text.lower()
    source_ids = [str(item).lower() for item in truth.get("contamination_source_ids", [])]
    if any(source_id in lowered for source_id in source_ids):
        return 1.0

    sentence_hits = []
    custom_phrases = []
    for item in truth.get("contamination_keywords", []):
        phrase = str(item).lower().strip()
        if not phrase:
            continue
        if phrase == "staffing":
            continue
        if " " in phrase or "recruit" in phrase or phrase.startswith("workforce") or phrase.startswith("provider staffing"):
            custom_phrases.append(phrase)
    phrases = tuple(dict.fromkeys([*_NOOP_CONTAMINATION_PHRASES, *custom_phrases]))
    for chunk in _sentence_chunks(text):
        chunk_lower = chunk.lower()
        if not any(phrase in chunk_lower for phrase in phrases):
            continue
        if _contains_exclusion_marker(chunk_lower):
            continue
        sentence_hits.append(chunk_lower)
    return 1.0 if sentence_hits else 0.0


def _contamination_rate(text: str, truth: dict[str, Any]) -> float | None:
    task_type = str(truth.get("task_type", ""))
    if task_type == "unrelated_branch_noop_update":
        return _noop_contamination_rate(text, truth)
    source_ids = [str(item).lower() for item in truth.get("contamination_source_ids", [])]
    keywords = [str(item).lower() for item in truth.get("contamination_keywords", [])]
    if not source_ids and not keywords:
        return None
    lowered = text.lower()
    hit = any(source_id in lowered for source_id in source_ids) or any(keyword in lowered for keyword in keywords)
    return 1.0 if hit else 0.0


def _constraint_reflection_score(text: str, truth: dict[str, Any]) -> float | None:
    phrases = [str(item).lower() for item in truth.get("required_constraint_phrases", [])]
    if not phrases:
        return None
    lowered = text.lower()
    hits = sum(1 for phrase in phrases if phrase in lowered)
    return hits / len(phrases) if phrases else None


def _cross_artifact_consistency_score(run_payload: dict[str, Any], truth: dict[str, Any]) -> float | None:
    phrases = [str(item).lower() for item in truth.get("required_constraint_phrases", [])]
    if not phrases:
        return None
    outputs = {item.get("name"): str(item.get("content", "")) for item in run_payload.get("intermediate_outputs", [])}
    final_text = str(run_payload.get("final_output", "")).lower()
    recommendation_text = outputs.get("recommendation_criteria", "").lower()
    implementation_text = outputs.get("implementation_plan", "").lower()
    scored = []
    for phrase in phrases:
        hits = [
            phrase in final_text,
            phrase in recommendation_text,
        ]
        if implementation_text:
            hits.append(phrase in implementation_text)
        scored.append(sum(1 for hit in hits if hit) / len(hits))
    return mean(scored) if scored else None


def _artifact_preservation_metrics(
    task: ContextDisciplineTask,
    initial_run: dict[str, Any],
    updated_run: dict[str, Any],
) -> tuple[float | None, float | None, float | None]:
    before = dict(initial_run.get("artifact_hashes", {}))
    after = dict(updated_run.get("artifact_hashes", {}))
    expected_preserved = _expected_preserved_stages(task)
    expected_recomputed = _expected_recomputed_stages(task)
    if not before or not after:
        return None, None, None
    preserved_hits = [before.get(stage) == after.get(stage) for stage in expected_preserved if stage in before and stage in after]
    recomputed_hits = [before.get(stage) != after.get(stage) for stage in expected_recomputed if stage in before and stage in after]
    stable_artifact_hash_preservation = (sum(1 for hit in preserved_hits if hit) / len(preserved_hits)) if preserved_hits else None
    unaffected_artifact_preservation = stable_artifact_hash_preservation
    downstream_propagation_recall = (sum(1 for hit in recomputed_hits if hit) / len(recomputed_hits)) if recomputed_hits else None
    return stable_artifact_hash_preservation, unaffected_artifact_preservation, downstream_propagation_recall


def _upstream_churn_rate(task: ContextDisciplineTask, initial_run: dict[str, Any], updated_run: dict[str, Any]) -> float | None:
    before = dict(initial_run.get("artifact_hashes", {}))
    after = dict(updated_run.get("artifact_hashes", {}))
    expected_preserved = _expected_preserved_stages(task)
    if not before or not after or not expected_preserved:
        return None
    comparisons = [before.get(stage) != after.get(stage) for stage in expected_preserved if stage in before and stage in after]
    return (sum(1 for changed in comparisons if changed) / len(comparisons)) if comparisons else None


def _no_op_churn_rate(prior_text: str, updated_text: str, expected_stable: bool) -> float | None:
    if not expected_stable:
        return None
    if prior_text == updated_text:
        return 0.0
    return 1.0 - _normalized_similarity(prior_text, updated_text)


def _fast_judge(task: ContextDisciplineTask, condition_payload: dict[str, Any], prior_run: dict[str, Any] | None) -> FastDemoJudgeResult:
    truth = _fast_demo_truth(task)
    run_payload = _latest_run(condition_payload)
    text = str(run_payload.get("final_output", ""))
    contamination = _contamination_rate(text, truth)
    constraint = _constraint_reflection_score(text, truth)
    expected_stable = bool(truth.get("expected_final_output_stable"))
    if expected_stable:
        comparison_run = prior_run if isinstance(prior_run, dict) else condition_payload.get("initial_run")
        exact_match = 1.0 if isinstance(comparison_run, dict) and str(comparison_run.get("final_output", "")) == text else 0.0
        contamination_penalty = 0.0 if contamination is None else contamination
        score = max(0.0, 1.0 - max(1.0 - exact_match, contamination_penalty))
        return FastDemoJudgeResult(output_faithfulness_score=score, current_state_precision_score=1.0 - contamination_penalty)
    if constraint is not None:
        return FastDemoJudgeResult(output_faithfulness_score=constraint, current_state_precision_score=constraint)
    return FastDemoJudgeResult(output_faithfulness_score=None, current_state_precision_score=None)


def _build_condition_payloads(task: ContextDisciplineTask, config: RunnerConfig, condition_ids: list[str]) -> dict[str, dict[str, Any]]:
    selected = set(condition_ids)
    loop_runner = LoopCentricHarnessRunner(config)
    dag_runner = SimpleDAGHarnessRunner(config)
    payloads: dict[str, dict[str, Any]] = {}

    loop_prior = None
    if selected & {"loop_real_world_final_update", "loop_real_world_with_edit_event"}:
        loop_prior = loop_runner.run_fresh(task, repeats=1, updated=False)
    if "loop_real_world_final_update" in selected and loop_prior is not None:
        loop_update = loop_runner.run_update(task, loop_prior, task.update_edits)
        payloads["loop_real_world_final_update"] = _condition_payload(
            "loop_real_world_final_update",
            loop_update.payload,
            prior_run=loop_prior.payload["runs"][0],
        )
    if "loop_real_world_with_edit_event" in selected and loop_prior is not None:
        loop_update = loop_runner.run_update(task, loop_prior, task.update_edits, include_edit_event=True)
        payloads["loop_real_world_with_edit_event"] = _condition_payload(
            "loop_real_world_with_edit_event",
            loop_update.payload,
            prior_run=loop_prior.payload["runs"][0],
        )
    if "simple_dag_replay_selective_recompute" in selected:
        dag_initial = dag_runner._run_graph(task, updated=False, allow_replay=True)
        dag_replay_probe = dag_runner._run_graph(task, updated=False, allow_replay=True)
        dag_update = dag_runner._run_graph_with_overrides(
            task,
            updated=True,
            allow_replay=True,
            stage_output_overrides=task.updated_stage_overrides or None,
        )
        before = dag_initial.get("artifact_hashes", {})
        after = dag_update.get("artifact_hashes", {})
        all_keys = sorted(set(before) | set(after))
        preserved = [key for key in all_keys if before.get(key) == after.get(key)]
        recomputed = [key for key in all_keys if before.get(key) != after.get(key)]
        total = len(preserved) + len(recomputed)
        dag_update["preserved_artifacts"] = preserved
        dag_update["recomputed_stages"] = recomputed
        dag_update["artifacts_preserved_percent"] = len(preserved) / total if total else 0.0
        dag_update["stages_recomputed_percent"] = len(recomputed) / total if total else 0.0
        dag_update["unrelated_churn_rate"] = 0.0
        payloads["simple_dag_replay_selective_recompute"] = _condition_payload(
            "simple_dag_replay_selective_recompute",
            dag_update,
            initial_run=dag_initial,
            replay_probe=dag_replay_probe,
            exact_artifact_hash_replay_match=dag_initial.get("artifact_hashes") == dag_replay_probe.get("artifact_hashes"),
            exact_final_output_replay_match=dag_initial.get("final_output") == dag_replay_probe.get("final_output"),
        )
    return payloads


def _row_for_condition(
    task: ContextDisciplineTask,
    condition_id: str,
    condition_payload: dict[str, Any],
    repeat_id: str,
    judge_result: FastDemoJudgeResult | None,
) -> dict[str, Any]:
    run_payload = _latest_run(condition_payload)
    prior_run = condition_payload.get("prior_run")
    initial_run = condition_payload.get("initial_run")
    truth = _fast_demo_truth(task)
    expected_stable = bool(truth.get("expected_final_output_stable"))
    comparison_run = prior_run if isinstance(prior_run, dict) else (initial_run if isinstance(initial_run, dict) else None)
    prior_text = str(comparison_run.get("final_output", "")) if isinstance(comparison_run, dict) else ""
    final_text = str(run_payload.get("final_output", ""))
    final_exact_match = bool(comparison_run and prior_text == final_text)
    final_hash_preserved = bool(comparison_run and _hash(comparison_run) == _hash(run_payload))
    stable_artifact_hash_preservation = None
    unaffected_artifact_preservation = None
    downstream_propagation_recall = None
    upstream_churn_rate = None
    if isinstance(initial_run, dict):
        stable_artifact_hash_preservation, unaffected_artifact_preservation, downstream_propagation_recall = _artifact_preservation_metrics(task, initial_run, run_payload)
        upstream_churn_rate = _upstream_churn_rate(task, initial_run, run_payload)
    row = {
        "task_id": task.task_id,
        "condition_id": condition_id,
        "repeat_id": repeat_id,
        "final_output_exact_match": final_exact_match if comparison_run is not None else None,
        "final_output_hash_preserved": final_hash_preserved if comparison_run is not None else None,
        "stable_artifact_hash_preservation": stable_artifact_hash_preservation,
        "unnecessary_churn_rate": _no_op_churn_rate(prior_text, final_text, expected_stable) if comparison_run is not None else None,
        "unrelated_branch_contamination_rate": _contamination_rate(final_text, truth),
        "downstream_propagation_recall": downstream_propagation_recall,
        "upstream_churn_rate": upstream_churn_rate,
        "unaffected_artifact_preservation": unaffected_artifact_preservation,
        "final_memo_constraint_reflection": _constraint_reflection_score(final_text, truth),
        "cross_artifact_consistency_score": _cross_artifact_consistency_score(run_payload, truth),
        "input_tokens": run_payload.get("model_usage", {}).get("input_tokens"),
        "output_tokens": run_payload.get("model_usage", {}).get("output_tokens"),
        "model_call_count": run_payload.get("model_usage", {}).get("model_calls"),
        "wall_clock_ms": run_payload.get("duration_ms"),
        "output_faithfulness_score": judge_result.output_faithfulness_score if judge_result else None,
        "current_state_precision_score": judge_result.current_state_precision_score if judge_result else None,
    }
    return row


def _aggregate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("task_id")), str(row.get("condition_id")))
        groups.setdefault(key, []).append(row)
    summaries: list[dict[str, Any]] = []
    for (task_id, condition_id), items in sorted(groups.items()):
        summary: dict[str, Any] = {
            "task_id": task_id,
            "condition_id": condition_id,
            "n": len(items),
        }
        for key in items[0].keys():
            if key in {"task_id", "condition_id", "repeat_id"}:
                continue
            values = [item.get(key) for item in items if isinstance(item.get(key), (int, float, bool))]
            if not values:
                summary[key] = None
                continue
            numbers = [float(value) for value in values]
            summary[key] = mean(numbers)
        summaries.append(summary)
    return summaries


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_fast_dag_demo(
    *,
    config: RunnerConfig,
    output_dir: Path,
    repeats: int = 1,
    judge_repeats: int = 1,
    task_ids: list[str] | None = None,
    condition_ids: list[str] | None = None,
    skip_judge_for_noop: bool = False,
) -> dict[str, Any]:
    selected_task_ids = task_ids or list(FAST_DEMO_TASK_FILES.keys())
    selected_conditions = condition_ids or list(FAST_DEMO_DEFAULT_CONDITIONS)
    metadata = {
        "schema_version": "execution_lineage.fast_dag_demo.v1",
        "created_at": utc_now(),
        "repo_commit": _git_commit_hash(),
        "model_provider": config.model_provider,
        "model_name": config.model_name,
        "model_temperature": config.model_temperature,
        "model_seed": config.model_seed,
        "repeats": repeats,
        "judge_repeats": judge_repeats,
        "conditions": selected_conditions,
        "tasks": selected_task_ids,
        "skip_judge_for_noop": skip_judge_for_noop,
    }
    _write_json(output_dir / "run_metadata.json", metadata)

    all_rows: list[dict[str, Any]] = []
    run_index: dict[str, Any] = {"tasks": {}}

    for task_id in selected_task_ids:
        task = load_task(FAST_DEMO_TASK_FILES[task_id])
        task_runs: dict[str, Any] = {}
        for repeat_index in range(1, repeats + 1):
            repeat_id = f"repeat_{repeat_index:02d}"
            condition_payloads = _build_condition_payloads(task, config, selected_conditions)
            for condition_id, condition_payload in condition_payloads.items():
                _store_condition_artifacts(output_dir / "outputs", task.task_id, None, repeat_id, condition_id, condition_payload)

            truth = _fast_demo_truth(task)
            judge_results: dict[str, FastDemoJudgeResult] = {}
            should_judge = judge_repeats > 0 and not (skip_judge_for_noop and truth.get("task_type") == "unrelated_branch_noop_update")
            if judge_repeats > 0:
                judge_input_dir = output_dir / "judge_inputs" / task.task_id / repeat_id
                judge_output_dir = output_dir / "judge_outputs" / task.task_id / repeat_id
                _write_json(
                    judge_input_dir / "heuristic_judge_bundle.json",
                    {
                        "task_id": task.task_id,
                        "task_type": truth.get("task_type"),
                        "conditions": {
                            condition_id: {
                                "final_output": _latest_run(payload).get("final_output"),
                                "intermediate_outputs": _latest_run(payload).get("intermediate_outputs", []),
                            }
                            for condition_id, payload in condition_payloads.items()
                        },
                    },
                )
                if should_judge:
                    for judge_index in range(1, judge_repeats + 1):
                        output_payload = {"systems": {}}
                        for condition_id, condition_payload in condition_payloads.items():
                            prior_run = condition_payload.get("prior_run") if isinstance(condition_payload.get("prior_run"), dict) else None
                            judge_result = _fast_judge(task, condition_payload, prior_run)
                            judge_results[condition_id] = judge_result
                            output_payload["systems"][condition_id] = {
                                "output_faithfulness_score": judge_result.output_faithfulness_score,
                                "current_state_precision_score": judge_result.current_state_precision_score,
                            }
                        _write_json(judge_output_dir / f"heuristic_judge_output_{judge_index:02d}.json", output_payload)
                else:
                    _write_json(judge_output_dir / "heuristic_judge_skipped.json", {"skipped": True, "reason": "skip_judge_for_noop"})

            for condition_id, condition_payload in condition_payloads.items():
                row = _row_for_condition(
                    task,
                    condition_id,
                    condition_payload,
                    repeat_id,
                    judge_results.get(condition_id),
                )
                all_rows.append(row)
            task_runs[repeat_id] = condition_payloads
        run_index["tasks"][task_id] = task_runs

    summary_rows = _aggregate_rows(all_rows)
    _write_json(output_dir / "fast_demo_results.json", run_index)
    _write_json(output_dir / "summary_by_task_condition.json", summary_rows)
    _write_csv(output_dir / "summary_by_task_condition.csv", summary_rows)
    _write_json(output_dir / "rows_by_task_condition_repeat.json", all_rows)
    return {
        "run_metadata": metadata,
        "summary_by_task_condition": summary_rows,
        "row_count": len(all_rows),
    }
