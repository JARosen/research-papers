from __future__ import annotations

import argparse
import random
from pathlib import Path

from metric_lib import read_json, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build anonymized judge bundles.")
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--results-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["output_only", "traceability"], default="output_only")
    parser.add_argument("--conditions", nargs="*", help="Optional explicit conditions to include.")
    parser.add_argument("--seed", type=int, default=7)
    return parser.parse_args()


def _latest_payload(condition_payload: dict) -> dict:
    if "updated_run" in condition_payload:
        return dict(condition_payload["updated_run"])
    runs = condition_payload.get("runs", [])
    if runs:
        return dict(runs[-1])
    return dict(condition_payload)


def main() -> None:
    args = parse_args()
    task = read_json(args.task_dir / "task.json")
    sources = read_json(args.task_dir / "sources.json")
    edits = read_json(args.task_dir / "edits.json")
    ground_truth = read_json(args.task_dir / "ground_truth.json")
    results = read_json(args.results_file)

    conditions = results.get("conditions", {})
    selected = args.conditions or sorted(conditions.keys())
    labels = ["System A", "System B", "System C", "System D", "System E", "System F", "System G"]
    random.Random(args.seed).shuffle(labels)
    mapping = {labels[index]: condition_id for index, condition_id in enumerate(selected)}

    systems = []
    for anonymous_id, condition_id in mapping.items():
        payload = _latest_payload(conditions[condition_id])
        system_payload = {
            "anonymous_id": anonymous_id,
            "condition_id": "hidden",
            "final_output": payload.get("final_output"),
        }
        if args.mode == "traceability":
            transcript = payload.get("conversation_transcript", [])
            system_payload["intermediate_outputs"] = payload.get("intermediate_outputs", [])
            system_payload["conversation_transcript_excerpt"] = transcript[:6]
            system_payload["execution_metadata"] = payload.get("execution_metadata")
            system_payload["memory_metadata"] = payload.get("memory_metadata")
        systems.append(system_payload)

    bundle = {
        "bundle_id": f"judge_{args.mode}_{task['task_id']}",
        "judge_mode": args.mode,
        "task_id": task["task_id"],
        "task_instruction": task["instruction"],
        "source_bundle": sources["sources"],
        "controlled_hazards": {
            "irrelevant_decoys": [item["id"] for item in sources["sources"] if item.get("kind") == "irrelevant_decoy"],
            "superseded": [item["id"] for item in sources["sources"] if item.get("status") == "superseded"],
            "conflicting_pairs": ground_truth["sources"]["conflicting_pairs"],
        },
        "upstream_edit": edits["edits"][0] if edits.get("edits") else None,
        "systems": systems,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / f"judge_bundle_{args.mode}.json", bundle)
    write_json(args.output_dir / f"system_mapping_{args.mode}.json", mapping)


if __name__ == "__main__":
    main()
