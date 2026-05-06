from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .config import RunnerConfig, load_environment
from .fast_dag_demo import FAST_DEMO_DEFAULT_CONDITIONS, FAST_DEMO_TASK_FILES, run_fast_dag_demo
from .harnesses.base import HarnessRunResult
from .harnesses.loop_centric.runner import LoopCentricHarnessRunner
from .harnesses.simple_dag.runner import SimpleDAGHarnessRunner
from .harnesses.thruwire.runner import ThruWireHarnessRunner
from .metrics import summarize_texts
from .paper_minimal import (
    DEFAULT_TASK_FILES,
    PAPER_CONDITIONS,
    PAPER_DIAGNOSTIC_CONDITIONS,
    run_paper_minimal,
)
from .tasks import ContextDisciplineTask, load_task


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Context-discipline experiment runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("bootstrap-chatgpt", help="Open a persistent ChatGPT browser profile for manual login")

    run = subparsers.add_parser("run", help="Run configured experiment conditions")
    run.add_argument("--task-file", type=Path, required=True)
    run.add_argument(
        "--rq1-repeats",
        type=int,
        default=3,
        help="Repeat count for RQ1 fresh/replay stability runs only.",
    )
    run.add_argument(
        "--repeats",
        type=int,
        help="Deprecated alias for --rq1-repeats.",
    )
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument(
        "--conditions",
        nargs="*",
        help="Optional explicit condition list. Defaults to experiment config defaults.",
    )
    run.add_argument(
        "--include-optional",
        action="store_true",
        help="Include optional conditions such as chatgpt_product_selenium.",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing output directory by skipping completed conditions.",
    )

    paper = subparsers.add_parser("run-paper-minimal", help="Run the minimal paper experiment package")
    paper.add_argument("--output-dir", type=Path, required=True)
    paper.add_argument("--repeats", type=int)
    paper.add_argument("--judge-repeats", type=int)
    paper.add_argument(
        "--mode",
        choices=["full", "diagnostic"],
        default="full",
        help="Diagnostic mode runs a trimmed condition set with quick defaults for fast preflight checks.",
    )
    paper.add_argument(
        "--conditions",
        nargs="*",
        choices=sorted(PAPER_CONDITIONS),
        help="Optional explicit paper condition subset. Overrides the mode defaults.",
    )
    paper.add_argument(
        "--tasks",
        nargs="*",
        choices=sorted(DEFAULT_TASK_FILES.keys()),
        help="Optional subset of paper-minimal task ids.",
    )

    fast_demo = subparsers.add_parser("run-fast-dag-demo", help="Run the fast DAG-focused demo package")
    fast_demo.add_argument("--output-dir", type=Path, required=True)
    fast_demo.add_argument("--repeats", type=int, default=1)
    fast_demo.add_argument("--judge-repeats", type=int, default=1)
    fast_demo.add_argument(
        "--tasks",
        nargs="*",
        choices=sorted(FAST_DEMO_TASK_FILES.keys()),
        help="Optional subset of fast demo task ids.",
    )
    fast_demo.add_argument(
        "--conditions",
        nargs="*",
        choices=sorted(
            [
                "loop_real_world_final_update",
                "loop_real_world_with_edit_event",
                "simple_dag_replay_selective_recompute",
            ]
        ),
        help="Optional explicit fast-demo condition subset.",
    )
    fast_demo.add_argument(
        "--skip-judge-for-noop",
        action="store_true",
        help="Skip heuristic judging for the unrelated-branch no-op task.",
    )
    return parser.parse_args()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _sanitize_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned or "item"


def _render_prompt_entry(entry: dict[str, Any], index: int) -> dict[str, Any]:
    step_name = str(entry.get("step_name") or f"call_{index:02d}")
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
        "step_name": step_name,
        "instructions": instructions,
        "input_items": input_items,
        "response_id": entry.get("response_id"),
        "rendered_markdown": rendered_markdown,
        "sha256": hashlib.sha256(rendered_markdown.encode("utf-8")).hexdigest(),
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


def _attach_rendered_prompts(results: dict[str, Any]) -> None:
    for payload in results.values():
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("runs"), list):
            for run in payload["runs"]:
                if isinstance(run, dict):
                    _attach_rendered_prompts_to_run(run)
        if isinstance(payload.get("updated_run"), dict):
            _attach_rendered_prompts_to_run(payload["updated_run"])
        _attach_rendered_prompts_to_run(payload)


def _write_rendered_prompts(output_dir: Path, bundle: dict[str, Any]) -> None:
    root = output_dir / "rendered_prompts"
    if root.exists():
        shutil.rmtree(root)
    conditions = bundle.get("conditions", {})
    for condition_name, payload in conditions.items():
        if not isinstance(payload, dict):
            continue
        condition_dir = root / _sanitize_name(condition_name)
        targets: list[tuple[str, dict[str, Any]]] = []
        if isinstance(payload.get("runs"), list):
            for index, run in enumerate(payload["runs"], start=1):
                if isinstance(run, dict):
                    targets.append((f"run_{index:02d}", run))
        if isinstance(payload.get("updated_run"), dict):
            targets.append(("updated_run", payload["updated_run"]))
        if isinstance(payload.get("rendered_prompts"), list):
            targets.append(("condition", payload))
        if not targets:
            continue
        for run_label, run_payload in targets:
            prompts = run_payload.get("rendered_prompts", [])
            if not isinstance(prompts, list):
                continue
            run_dir = condition_dir / run_label
            for item in prompts:
                if not isinstance(item, dict):
                    continue
                filename = f"{item.get('call_name', 'prompt')}.md"
                (run_dir / filename).parent.mkdir(parents=True, exist_ok=True)
                (run_dir / filename).write_text(str(item.get("rendered_markdown", "")))


def _build_bundle(task: ContextDisciplineTask, config: RunnerConfig, results: dict[str, Any]) -> dict[str, Any]:
    _attach_rendered_prompts(results)
    return {
        "schema_version": "execution_lineage.result.v2",
        "task_id": task.task_id,
        "default_conditions": list(config.default_conditions),
        "optional_conditions": list(config.optional_conditions),
        "disabled_conditions": list(config.disabled_conditions),
        "conditions": results,
        "summary_by_rq": _build_summary(results),
    }


def _write_bundle(output_dir: Path, task: ContextDisciplineTask, config: RunnerConfig, results: dict[str, Any]) -> None:
    bundle = _build_bundle(task, config, results)
    _write_json(output_dir / "results.json", bundle)
    _write_rendered_prompts(output_dir, bundle)


def _load_existing_conditions(output_dir: Path, task_id: str) -> dict[str, Any]:
    results_path = output_dir / "results.json"
    if not results_path.exists():
        return {}
    payload = json.loads(results_path.read_text())
    if payload.get("task_id") != task_id:
        raise RuntimeError(
            f"Existing results at {results_path} belong to task {payload.get('task_id')}, "
            f"but the requested task is {task_id}."
        )
    return dict(payload.get("conditions", {}))


def _condition_order(config: RunnerConfig, requested: list[str] | None, include_optional: bool) -> list[str]:
    if requested:
        return requested
    conditions = list(config.default_conditions)
    if include_optional:
        conditions.extend(config.optional_conditions)
    return conditions


def _graph_condition_payload(results: dict[str, Any], fresh_key: str, replay_key: str) -> tuple[Any, Any]:
    return results.get(fresh_key), results.get(replay_key)


def _build_paired_comparisons(results: dict[str, Any]) -> dict[str, Any]:
    graph_fresh = results.get("simple_dag_fresh_recompute") or results.get("thruwire_fresh_recompute")
    graph_update = results.get("simple_dag_replay_selective_recompute") or results.get("thruwire_replay_selective_recompute")
    paired: dict[str, Any] = {}
    if results.get("loop_centric_fresh") and graph_fresh:
        paired["loop_fresh_vs_dag_fresh"] = {
            "left_condition": "loop_centric_fresh",
            "right_condition": graph_fresh.get("condition_name"),
            "rq": ["RQ1", "RQ3"],
        }
    if results.get("loop_real_world_final_update") and graph_update:
        paired["loop_real_world_final_update_vs_dag_update"] = {
            "left_condition": "loop_real_world_final_update",
            "right_condition": graph_update.get("condition_name"),
            "rq": ["RQ2", "RQ3"],
        }
    if results.get("loop_real_world_with_edit_event") and graph_update:
        paired["loop_real_world_with_edit_event_vs_dag_update"] = {
            "left_condition": "loop_real_world_with_edit_event",
            "right_condition": graph_update.get("condition_name"),
            "rq": ["RQ2", "RQ3"],
        }
    if results.get("loop_real_world_with_notes") and graph_update:
        paired["loop_real_world_with_notes_vs_dag_update"] = {
            "left_condition": "loop_real_world_with_notes",
            "right_condition": graph_update.get("condition_name"),
            "rq": ["RQ2", "RQ3"],
        }
    if results.get("loop_real_world_with_memory") and graph_update:
        paired["loop_real_world_with_memory_vs_dag_update"] = {
            "left_condition": "loop_real_world_with_memory",
            "right_condition": graph_update.get("condition_name"),
            "rq": ["RQ2", "RQ3"],
        }
    if results.get("loop_real_world_staged_update") and graph_update:
        paired["loop_real_world_staged_update_vs_dag_update"] = {
            "left_condition": "loop_real_world_staged_update",
            "right_condition": graph_update.get("condition_name"),
            "rq": ["RQ2", "RQ3"],
        }
    return paired


def _build_summary(results: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    loop_fresh = results.get("loop_centric_fresh")
    graph_fresh, graph_replay = _graph_condition_payload(
        results,
        "simple_dag_fresh_recompute",
        "simple_dag_replay_selective_recompute",
    )
    if not (graph_fresh and graph_replay):
        graph_fresh, graph_replay = _graph_condition_payload(
            results,
            "thruwire_fresh_recompute",
            "thruwire_replay_selective_recompute",
        )
    if loop_fresh and graph_fresh and graph_replay:
        loop_texts = [item["final_output"] for item in loop_fresh.get("runs", [])]
        graph_fresh_texts = [item["final_output"] for item in graph_fresh.get("runs", [])]
        graph_replay_texts = [item["final_output"] for item in graph_replay.get("runs", [])]
        summary["RQ1"] = {
            "loop_centric_fresh_variation": summarize_texts(loop_texts),
            "graph_fresh_variation": summarize_texts(graph_fresh_texts),
            "graph_replay_stability": summarize_texts(graph_replay_texts),
            "primary_success_criterion": "exact replay under unchanged execution identity",
        }
    update_result = (
        results.get("simple_dag_replay_selective_recompute", {}).get("updated_run")
        or results.get("thruwire_replay_selective_recompute", {}).get("updated_run")
    )
    if update_result:
        summary["RQ2"] = {
            "stages_recomputed_percent": update_result.get("stages_recomputed_percent"),
            "artifacts_preserved_percent": update_result.get("artifacts_preserved_percent"),
            "manual_context_reconstruction_actions": {
                "loop_real_world_final_update": results.get("loop_real_world_final_update", {})
                .get("execution_metadata", {})
                .get("manual_context_reconstruction_actions"),
                "loop_real_world_with_edit_event": results.get("loop_real_world_with_edit_event", {})
                .get("execution_metadata", {})
                .get("manual_context_reconstruction_actions"),
                "loop_real_world_with_notes": results.get("loop_real_world_with_notes", {})
                .get("execution_metadata", {})
                .get("manual_context_reconstruction_actions"),
                "loop_real_world_with_memory": results.get("loop_real_world_with_memory", {})
                .get("updated_run", {})
                .get("execution_metadata", {})
                .get("manual_context_reconstruction_actions"),
                "loop_real_world_staged_update": results.get("loop_real_world_staged_update", {})
                .get("execution_metadata", {})
                .get("manual_context_reconstruction_actions"),
            },
        }
    summary["RQ3"] = {
        "judge_modes": ["traceability"],
        "judge_bundle_builder": "experiments/execution_lineage/scripts/build_judge_bundle.py",
    }
    summary["paired_comparisons"] = _build_paired_comparisons(results)
    return summary


async def _run_conditions(
    task: ContextDisciplineTask,
    config: RunnerConfig,
    conditions: list[str],
    rq1_repeats: int,
    existing_results: dict[str, Any],
    checkpoint: Any | None = None,
) -> dict[str, Any]:
    results: dict[str, Any] = dict(existing_results)
    loop_runner = LoopCentricHarnessRunner(config)
    simple_dag_runner = SimpleDAGHarnessRunner(config)

    loop_fresh_result = None
    if "loop_centric_fresh" in results:
        loop_fresh_result = HarnessRunResult(payload=dict(results["loop_centric_fresh"]))
    if any(
        item in conditions
        for item in [
            "loop_centric_fresh",
            "loop_real_world_final_update",
            "loop_real_world_with_edit_event",
            "loop_real_world_with_notes",
            "loop_real_world_with_memory",
            "loop_real_world_staged_update",
        ]
    ):
        if loop_fresh_result is None:
            loop_repeats = rq1_repeats if "loop_centric_fresh" in conditions else 1
            loop_fresh_result = loop_runner.run_fresh(task, repeats=loop_repeats)
            payload = dict(loop_fresh_result.payload)
            payload["condition_id"] = "C1"
            payload["condition_name"] = "loop_centric_fresh"
            results["loop_centric_fresh"] = payload
            if checkpoint is not None:
                checkpoint(results)
    if "loop_real_world_final_update" in conditions and loop_fresh_result is not None and "loop_real_world_final_update" not in results:
        update = loop_runner.run_update(task, loop_fresh_result, task.primary_edit)
        payload = dict(update.payload)
        payload["condition_id"] = "C2"
        payload["condition_name"] = "loop_real_world_final_update"
        payload["condition"] = "loop_real_world_final_update"
        results["loop_real_world_final_update"] = payload
        if checkpoint is not None:
            checkpoint(results)
    if "loop_real_world_with_edit_event" in conditions and loop_fresh_result is not None and "loop_real_world_with_edit_event" not in results:
        update = loop_runner.run_update(task, loop_fresh_result, task.primary_edit, include_edit_event=True)
        payload = dict(update.payload)
        payload["condition_id"] = "C11"
        payload["condition_name"] = "loop_real_world_with_edit_event"
        payload["condition"] = "loop_real_world_with_edit_event"
        results["loop_real_world_with_edit_event"] = payload
        if checkpoint is not None:
            checkpoint(results)
    if "loop_real_world_with_notes" in conditions and loop_fresh_result is not None and "loop_real_world_with_notes" not in results:
        update = loop_runner.run_update(task, loop_fresh_result, task.primary_edit, include_intermediates=True)
        payload = dict(update.payload)
        payload["condition_id"] = "C3"
        payload["condition_name"] = "loop_real_world_with_notes"
        payload["condition"] = "loop_real_world_with_notes"
        results["loop_real_world_with_notes"] = payload
        if checkpoint is not None:
            checkpoint(results)
    if "loop_real_world_with_memory" in conditions and "loop_real_world_with_memory" not in results:
        memory_fresh_result = loop_runner.run_fresh_with_procedural_memory(task, repeats=1)
        memory_payload = dict(memory_fresh_result.payload)
        memory_payload["condition_id"] = "C4"
        memory_payload["condition_name"] = "loop_real_world_with_memory"
        memory_payload["condition"] = "loop_real_world_with_memory"
        memory_update = loop_runner.run_update(
            task,
            memory_fresh_result,
            task.primary_edit,
            include_intermediates=True,
            use_procedural_memory=True,
        )
        memory_payload["updated_run"] = memory_update.payload
        results["loop_real_world_with_memory"] = memory_payload
        if checkpoint is not None:
            checkpoint(results)
    if "loop_real_world_staged_update" in conditions and loop_fresh_result is not None and "loop_real_world_staged_update" not in results:
        update = loop_runner.run_staged_update(task, loop_fresh_result, task.primary_edit)
        payload = dict(update.payload)
        payload["condition_id"] = "C10"
        payload["condition_name"] = "loop_real_world_staged_update"
        payload["condition"] = "loop_real_world_staged_update"
        results["loop_real_world_staged_update"] = payload
        if checkpoint is not None:
            checkpoint(results)
    if (
        ("simple_dag_fresh_recompute" in conditions and "simple_dag_fresh_recompute" not in results)
        or (
            "simple_dag_replay_selective_recompute" in conditions
            and "simple_dag_replay_selective_recompute" not in results
        )
    ):
        simple_dag_results = simple_dag_runner.run_all(
            task,
            replay_repeats=rq1_repeats if "simple_dag_replay_selective_recompute" in conditions else 1,
            fresh_repeats=rq1_repeats if "simple_dag_fresh_recompute" in conditions else 0,
            include_update="simple_dag_replay_selective_recompute" in conditions,
        )
        changed = False
        for name, result in simple_dag_results.items():
            if name in conditions:
                results[name] = result.payload
                changed = True
        if changed and checkpoint is not None:
            checkpoint(results)
    if (
        ("thruwire_fresh_recompute" in conditions and "thruwire_fresh_recompute" not in results)
        or (
            "thruwire_replay_selective_recompute" in conditions
            and "thruwire_replay_selective_recompute" not in results
        )
    ):
        thruwire_results = await ThruWireHarnessRunner(config).run_all(task, repeats=rq1_repeats)
        changed = False
        for name, result in thruwire_results.items():
            if name in conditions:
                results[name] = result.payload
                changed = True
        if changed and checkpoint is not None:
            checkpoint(results)
    if "chatgpt_product_selenium" in conditions and "chatgpt_product_selenium" not in results:
        from .harnesses.chatgpt_selenium.runner import ChatGPTProductSeleniumRunner

        selenium = ChatGPTProductSeleniumRunner(config).run_fresh(task, repeats=1)
        payload = dict(selenium.payload)
        payload["condition_id"] = "C9"
        payload["condition_name"] = "chatgpt_product_selenium"
        results["chatgpt_product_selenium"] = payload
        if checkpoint is not None:
            checkpoint(results)
    return results


def main() -> None:
    args = _parse_args()
    load_environment()
    config = RunnerConfig.from_env()

    if args.command == "bootstrap-chatgpt":
        from .chatgpt_backend import ChatGPTBaselineRunner

        ChatGPTBaselineRunner(config).bootstrap_login()
        return

    if args.command == "run-paper-minimal":
        selected_conditions = args.conditions or (
            list(PAPER_DIAGNOSTIC_CONDITIONS) if args.mode == "diagnostic" else list(PAPER_CONDITIONS)
        )
        repeats = args.repeats if args.repeats is not None else (1 if args.mode == "diagnostic" else 5)
        judge_repeats = args.judge_repeats if args.judge_repeats is not None else (0 if args.mode == "diagnostic" else 1)
        summary = run_paper_minimal(
            config=config,
            output_dir=args.output_dir,
            repeats=repeats,
            judge_repeats=judge_repeats,
            task_ids=args.tasks,
            condition_ids=selected_conditions,
            mode=args.mode,
        )
        print(json.dumps(summary, indent=2))
        return

    if args.command == "run-fast-dag-demo":
        summary = run_fast_dag_demo(
            config=config,
            output_dir=args.output_dir,
            repeats=args.repeats,
            judge_repeats=args.judge_repeats,
            task_ids=args.tasks,
            condition_ids=args.conditions or list(FAST_DEMO_DEFAULT_CONDITIONS),
            skip_judge_for_noop=args.skip_judge_for_noop,
        )
        print(json.dumps(summary, indent=2))
        return

    task = load_task(args.task_file)
    selected_conditions = _condition_order(config, args.conditions, args.include_optional)
    rq1_repeats = args.repeats if args.repeats is not None else args.rq1_repeats
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_results = _load_existing_conditions(output_dir, task.task_id) if args.resume else {}
    condition_results = asyncio.run(
        _run_conditions(
            task,
            config,
            selected_conditions,
            rq1_repeats,
            existing_results,
            checkpoint=lambda current: _write_bundle(output_dir, task, config, current),
        )
    )
    bundle = _build_bundle(task, config, condition_results)
    _write_json(output_dir / "results.json", bundle)
    _write_rendered_prompts(output_dir, bundle)
    print(json.dumps(bundle["summary_by_rq"], indent=2))


if __name__ == "__main__":
    main()
