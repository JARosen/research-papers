from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .config import RunnerConfig, load_environment
from .harnesses.chatgpt_selenium.runner import ChatGPTProductSeleniumRunner
from .harnesses.loop_centric.runner import LoopCentricHarnessRunner
from .harnesses.simple_dag.runner import SimpleDAGHarnessRunner
from .harnesses.thruwire.runner import ThruWireHarnessRunner
from .metrics import summarize_texts
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
    return parser.parse_args()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


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
    if results.get("loop_centric_update_final_only") and graph_update:
        paired["loop_update_final_only_vs_dag_update"] = {
            "left_condition": "loop_centric_update_final_only",
            "right_condition": graph_update.get("condition_name"),
            "rq": ["RQ2", "RQ3"],
        }
    if results.get("loop_centric_update_with_intermediates") and graph_update:
        paired["loop_update_with_intermediates_vs_dag_update"] = {
            "left_condition": "loop_centric_update_with_intermediates",
            "right_condition": graph_update.get("condition_name"),
            "rq": ["RQ2", "RQ3"],
        }
    if results.get("loop_centric_with_procedural_memory") and graph_update:
        paired["loop_memory_vs_dag_update"] = {
            "left_condition": "loop_centric_with_procedural_memory",
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
                "loop_centric_update_final_only": results.get("loop_centric_update_final_only", {})
                .get("execution_metadata", {})
                .get("manual_context_reconstruction_actions"),
                "loop_centric_update_with_intermediates": results.get("loop_centric_update_with_intermediates", {})
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
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    loop_runner = LoopCentricHarnessRunner(config)
    simple_dag_runner = SimpleDAGHarnessRunner(config)

    loop_fresh_result = None
    if any(
        item in conditions
        for item in [
            "loop_centric_fresh",
            "loop_centric_update_final_only",
            "loop_centric_update_with_intermediates",
            "loop_centric_with_procedural_memory",
        ]
    ):
        loop_repeats = rq1_repeats if "loop_centric_fresh" in conditions else 1
        loop_fresh_result = loop_runner.run_fresh(task, repeats=loop_repeats)
    if "loop_centric_fresh" in conditions and loop_fresh_result is not None:
        payload = dict(loop_fresh_result.payload)
        payload["condition_id"] = "C1"
        payload["condition_name"] = "loop_centric_fresh"
        results["loop_centric_fresh"] = payload
    if "loop_centric_update_final_only" in conditions and loop_fresh_result is not None:
        update = loop_runner.run_update(task, loop_fresh_result, task.primary_edit)
        payload = dict(update.payload)
        payload["condition_id"] = "C2"
        payload["condition_name"] = "loop_centric_update_final_only"
        payload["condition"] = "loop_centric_update_final_only"
        results["loop_centric_update_final_only"] = payload
    if "loop_centric_update_with_intermediates" in conditions and loop_fresh_result is not None:
        update = loop_runner.run_update(task, loop_fresh_result, task.primary_edit, include_intermediates=True)
        payload = dict(update.payload)
        payload["condition_id"] = "C3"
        payload["condition_name"] = "loop_centric_update_with_intermediates"
        payload["condition"] = "loop_centric_update_with_intermediates"
        results["loop_centric_update_with_intermediates"] = payload
    if "loop_centric_with_procedural_memory" in conditions:
        memory_fresh_result = loop_runner.run_fresh_with_procedural_memory(task, repeats=1)
        memory_payload = dict(memory_fresh_result.payload)
        memory_payload["condition_id"] = "C4"
        memory_payload["condition_name"] = "loop_centric_with_procedural_memory"
        memory_update = loop_runner.run_update(
            task,
            memory_fresh_result,
            task.primary_edit,
            include_intermediates=True,
            use_procedural_memory=True,
        )
        memory_payload["updated_run"] = memory_update.payload
        results["loop_centric_with_procedural_memory"] = memory_payload
    if "simple_dag_fresh_recompute" in conditions or "simple_dag_replay_selective_recompute" in conditions:
        simple_dag_results = simple_dag_runner.run_all(
            task,
            replay_repeats=rq1_repeats if "simple_dag_replay_selective_recompute" in conditions else 1,
            fresh_repeats=rq1_repeats if "simple_dag_fresh_recompute" in conditions else 0,
            include_update="simple_dag_replay_selective_recompute" in conditions,
        )
        for name, result in simple_dag_results.items():
            if name in conditions:
                results[name] = result.payload
    if "thruwire_fresh_recompute" in conditions or "thruwire_replay_selective_recompute" in conditions:
        thruwire_results = await ThruWireHarnessRunner(config).run_all(task, repeats=repeats)
        for name, result in thruwire_results.items():
            if name in conditions:
                results[name] = result.payload
    if "chatgpt_product_selenium" in conditions:
        selenium = ChatGPTProductSeleniumRunner(config).run_fresh(task, repeats=1)
        payload = dict(selenium.payload)
        payload["condition_id"] = "C9"
        payload["condition_name"] = "chatgpt_product_selenium"
        results["chatgpt_product_selenium"] = payload
    return results


def main() -> None:
    args = _parse_args()
    load_environment()
    config = RunnerConfig.from_env()

    if args.command == "bootstrap-chatgpt":
        from .chatgpt_backend import ChatGPTBaselineRunner

        ChatGPTBaselineRunner(config).bootstrap_login()
        return

    task = load_task(args.task_file)
    selected_conditions = _condition_order(config, args.conditions, args.include_optional)
    rq1_repeats = args.repeats if args.repeats is not None else args.rq1_repeats
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    condition_results = asyncio.run(_run_conditions(task, config, selected_conditions, rq1_repeats))
    bundle = {
        "schema_version": "execution_lineage.result.v2",
        "task_id": task.task_id,
        "default_conditions": list(config.default_conditions),
        "optional_conditions": list(config.optional_conditions),
        "disabled_conditions": list(config.disabled_conditions),
        "conditions": condition_results,
        "summary_by_rq": _build_summary(condition_results),
    }
    _write_json(output_dir / "results.json", bundle)
    print(json.dumps(bundle["summary_by_rq"], indent=2))


if __name__ == "__main__":
    main()
