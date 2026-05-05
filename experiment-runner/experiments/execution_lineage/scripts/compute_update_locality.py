from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from metric_lib import claim_ids, read_json


def changed_claim_metrics(claims: list[dict[str, Any]], ground_truth: dict[str, Any]) -> tuple[float, float, float]:
    affected = claim_ids(ground_truth, affected=True)
    unaffected = claim_ids(ground_truth, affected=False)
    predicted_changed = {
        str(item.get("claim_id") or item.get("claim_text") or "")
        for item in claims
        if item.get("affected_by_upstream_edit") is True or item.get("changed_appropriately") is True
    }
    regressed = {
        str(item.get("claim_id") or item.get("claim_text") or "")
        for item in claims
        if item.get("unaffected_regression") is True
    }
    true_positive = len(predicted_changed & affected)
    precision = true_positive / len(predicted_changed) if predicted_changed else 0.0
    recall = true_positive / len(affected) if affected else 1.0
    regression = len(regressed & unaffected) / len(unaffected) if unaffected else 0.0
    return precision, recall, regression


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute update-locality metrics.")
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--results-file", type=Path, required=True)
    parser.add_argument("--judge-output", type=Path, required=True)
    parser.add_argument("--mapping-file", type=Path, required=True)
    parser.add_argument("--condition-id", default="simple_dag_replay_selective_recompute")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ground_truth = read_json(args.task_dir / "ground_truth.json")
    results = read_json(args.results_file)
    judge_output = read_json(args.judge_output)
    mapping = read_json(args.mapping_file)

    condition = results["conditions"][args.condition_id]
    updated = condition.get("updated_run", {})
    anonymous_id = next(key for key, value in mapping.items() if value == args.condition_id)
    judged = next(item for item in judge_output["systems"] if item["anonymous_id"] == anonymous_id)
    precision, recall, regression = changed_claim_metrics(judged.get("claims", []), ground_truth)

    payload = {
        "stages_recomputed_count": len(updated.get("recomputed_stages", [])),
        "stages_recomputed_percent": updated.get("stages_recomputed_percent"),
        "artifacts_preserved_count": len(updated.get("preserved_artifacts", [])),
        "artifacts_preserved_percent": updated.get("artifacts_preserved_percent"),
        "unrelated_churn_rate": updated.get("unrelated_churn_rate"),
        "changed_claim_precision": precision,
        "changed_claim_recall": recall,
        "unaffected_claim_regression_rate": regression,
        "manual_context_reconstruction_actions": {
            "loop_centric_update_final_only": results["conditions"]
            .get("loop_centric_update_final_only", {})
            .get("execution_metadata", {})
            .get("manual_context_reconstruction_actions"),
            "loop_centric_update_with_intermediates": results["conditions"]
            .get("loop_centric_update_with_intermediates", {})
            .get("execution_metadata", {})
            .get("manual_context_reconstruction_actions"),
        },
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
