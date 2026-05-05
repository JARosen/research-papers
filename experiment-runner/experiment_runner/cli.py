from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from .config import REPO_DIR, RunnerConfig, load_environment
from .evaluator import OpenAIEvaluator
from .metrics import summarize_texts
from .tasks import ResearchTask, load_task


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research paper experiment runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("bootstrap-chatgpt", help="Open a persistent ChatGPT browser profile for manual login")

    run = subparsers.add_parser("run", help="Run both experiment arms and write results")
    run.add_argument("--task-file", type=Path, required=True)
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument(
        "--arms",
        choices=["both", "chatgpt", "thruwire"],
        default="both",
        help="Select which experiment arm(s) to run",
    )

    evaluate = subparsers.add_parser("evaluate", help="Run only the OpenAI evaluation on an existing result bundle")
    evaluate.add_argument("--task-file", type=Path, required=True)
    evaluate.add_argument("--input-dir", type=Path, required=True)

    return parser.parse_args()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _build_summary(chatgpt: Optional[dict[str, Any]], thruwire: Optional[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if chatgpt is not None and thruwire is not None:
        chatgpt_texts = [item["final_text"] for item in chatgpt["repeated_fresh_runs"]]
        replay_texts = [item["final_text"] for item in thruwire["replay_repeats"]]
        fresh_texts = [item["final_text"] for item in thruwire["fresh_repeats"]]
        summary = {
            "experiment_1_fresh_repeated_runs": {
                "chatgpt": {
                    "stability": summarize_texts(chatgpt_texts),
                    "mean_duration_s": sum(item["duration_s"] for item in chatgpt["repeated_fresh_runs"]) / len(chatgpt["repeated_fresh_runs"]),
                },
                "thruwire_fresh_recompute": {
                    "stability": summarize_texts(fresh_texts),
                    "mean_duration_s": sum(item["duration_s"] for item in thruwire["fresh_repeats"]) / len(thruwire["fresh_repeats"]),
                    "mean_executed_step_count": sum(item["executed_step_count"] for item in thruwire["fresh_repeats"]) / len(thruwire["fresh_repeats"]),
                },
            },
            "experiment_2_replay_enabled_repeated_runs": {
                "thruwire_replay_enabled": {
                    "stability": summarize_texts(replay_texts),
                    "mean_duration_s": sum(item["duration_s"] for item in thruwire["replay_repeats"]) / len(thruwire["replay_repeats"]),
                    "mean_executed_step_count": sum(item["executed_step_count"] for item in thruwire["replay_repeats"]) / len(thruwire["replay_repeats"]),
                    "execution_sources_by_run": [
                        item.get("execution_sources_by_step", {}) for item in thruwire["replay_repeats"]
                    ],
                }
            },
            "experiment_3_upstream_edit": {
                "chatgpt": {
                    "initial_duration_s": chatgpt["upstream_edit"]["initial"]["duration_s"],
                    "updated_duration_s": chatgpt["upstream_edit"]["updated"]["duration_s"],
                },
                "thruwire": {
                    "updated_duration_s": thruwire["updated"]["duration_s"],
                    "updated_executed_step_count": thruwire["updated"]["executed_step_count"],
                    "updated_execution_sources_by_step": thruwire["updated"].get("execution_sources_by_step", {}),
                },
            },
        }
    return summary


def _run_chatgpt(task: ResearchTask, output_dir: Path, config: RunnerConfig, repeats: int) -> dict[str, Any]:
    from .chatgpt_backend import ChatGPTBaselineRunner

    runner = ChatGPTBaselineRunner(config)
    repeated = runner.run_repeated_trials([task.baseline_prompt() for _ in range(repeats)])
    initial_for_update, updated = runner.run_initial_and_update(task.baseline_prompt(), task.update_prompt())
    payload = {
        "repeated_fresh_runs": [
            {
                "prompt": item.prompt,
                "final_text": item.final_text,
                "duration_s": item.duration_s,
                "conversation_url": item.conversation_url,
            }
            for item in repeated
        ],
        "upstream_edit": {
            "initial": {
                "prompt": initial_for_update.prompt,
                "final_text": initial_for_update.final_text,
                "duration_s": initial_for_update.duration_s,
                "conversation_url": initial_for_update.conversation_url,
            },
            "updated": {
                "prompt": updated.prompt,
                "final_text": updated.final_text,
                "duration_s": updated.duration_s,
                "conversation_url": updated.conversation_url,
            },
        },
    }
    _write_json(output_dir / "chatgpt_results.json", payload)
    return payload


async def _run_thruwire(task: ResearchTask, output_dir: Path, config: RunnerConfig, repeats: int) -> dict[str, Any]:
    from .thruwire_backend import ThruWireExperimentRunner

    runner = ThruWireExperimentRunner(config)
    payload = await runner.run_task(task, repeats)
    _write_json(output_dir / "thruwire_results.json", payload)
    return payload


def _run_evaluation(task: ResearchTask, output_dir: Path, config: RunnerConfig) -> dict[str, Any]:
    chatgpt_path = output_dir / "chatgpt_results.json"
    thruwire_path = output_dir / "thruwire_results.json"
    if not chatgpt_path.exists():
        raise RuntimeError(f"Missing required results file: {chatgpt_path}")
    if not thruwire_path.exists():
        raise RuntimeError(f"Missing required results file: {thruwire_path}")

    chatgpt = _read_json(chatgpt_path)
    thruwire = _read_json(thruwire_path)
    summary_path = output_dir / "summary.json"
    if summary_path.exists():
        summary = _read_json(summary_path)
    else:
        summary = _build_summary(chatgpt, thruwire)
        _write_json(summary_path, summary)

    payload = OpenAIEvaluator(config).evaluate(
        task=task,
        chatgpt_results=chatgpt,
        thruwire_results=thruwire,
        summary=summary,
        output_dir=output_dir,
    )
    return payload


def main() -> None:
    args = _parse_args()
    load_environment()
    config = RunnerConfig.from_env()

    if args.command == "bootstrap-chatgpt":
        from .chatgpt_backend import ChatGPTBaselineRunner

        ChatGPTBaselineRunner(config).bootstrap_login()
        return

    if args.command == "run":
        task = load_task(args.task_file)
        output_dir = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        chatgpt = None
        thruwire = None
        if args.arms in {"both", "chatgpt"}:
            chatgpt = _run_chatgpt(task, output_dir, config, args.repeats)
        if args.arms in {"both", "thruwire"}:
            thruwire = asyncio.run(_run_thruwire(task, output_dir, config, args.repeats))
        summary = _build_summary(chatgpt, thruwire)
        _write_json(output_dir / "summary.json", summary)
        if config.run_openai_evaluation and chatgpt is not None and thruwire is not None:
            try:
                OpenAIEvaluator(config).evaluate(
                    task=task,
                    chatgpt_results=chatgpt,
                    thruwire_results=thruwire,
                    summary=summary,
                    output_dir=output_dir,
                )
            except Exception as exc:
                _write_json(output_dir / "openai_evaluation_error.json", {"error": str(exc)})
                print(f"[evaluation] failed: {exc}")
        print(json.dumps(summary, indent=2))
        return

    if args.command == "evaluate":
        task = load_task(args.task_file)
        output_dir = args.input_dir
        try:
            payload = _run_evaluation(task, output_dir, config)
        except Exception as exc:
            _write_json(output_dir / "openai_evaluation_error.json", {"error": str(exc)})
            print(f"[evaluation] failed: {exc}")
            raise
        print(json.dumps(payload, indent=2))
        return


if __name__ == "__main__":
    main()
