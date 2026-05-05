from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from .chatgpt_backend import ChatGPTBaselineRunner
from .config import REPO_DIR, RunnerConfig, load_environment
from .metrics import summarize_texts
from .tasks import ResearchTask, load_task
from .thruwire_backend import ThruWireExperimentRunner


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

    return parser.parse_args()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _build_summary(chatgpt: Optional[dict[str, Any]], thruwire: Optional[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if chatgpt is not None:
        chatgpt_texts = [item["final_text"] for item in chatgpt["repeats"]]
        summary["chatgpt"] = {
            "stability": summarize_texts(chatgpt_texts),
            "mean_initial_duration_s": sum(item["duration_s"] for item in chatgpt["repeats"]) / len(chatgpt["repeats"]),
            "update_duration_s": chatgpt["updated"]["duration_s"],
        }
    if thruwire is not None:
        thruwire_texts = [item["final_text"] for item in thruwire["repeats"]]
        summary["thruwire"] = {
            "stability": summarize_texts(thruwire_texts),
            "mean_initial_duration_s": sum(item["duration_s"] for item in thruwire["repeats"]) / len(thruwire["repeats"]),
            "update_duration_s": thruwire["updated"]["duration_s"],
            "mean_executed_step_count": sum(item["executed_step_count"] for item in thruwire["repeats"]) / len(thruwire["repeats"]),
            "updated_executed_step_count": thruwire["updated"]["executed_step_count"],
        }
    return summary


def _run_chatgpt(task: ResearchTask, output_dir: Path, config: RunnerConfig, repeats: int) -> dict[str, Any]:
    runner = ChatGPTBaselineRunner(config)
    repeated = runner.run_repeated_trials([task.baseline_prompt() for _ in range(repeats)])
    initial_for_update, updated = runner.run_initial_and_update(task.baseline_prompt(), task.update_prompt())
    payload = {
        "repeats": [
            {
                "prompt": item.prompt,
                "final_text": item.final_text,
                "duration_s": item.duration_s,
                "conversation_url": item.conversation_url,
            }
            for item in repeated
        ],
        "initial_for_update": {
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
    }
    _write_json(output_dir / "chatgpt_results.json", payload)
    return payload


async def _run_thruwire(task: ResearchTask, output_dir: Path, config: RunnerConfig, repeats: int) -> dict[str, Any]:
    runner = ThruWireExperimentRunner(config)
    payload = await runner.run_task(task, repeats)
    _write_json(output_dir / "thruwire_results.json", payload)
    return payload


def main() -> None:
    args = _parse_args()
    load_environment()
    config = RunnerConfig.from_env()

    if args.command == "bootstrap-chatgpt":
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
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
