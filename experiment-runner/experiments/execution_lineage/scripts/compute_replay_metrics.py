from __future__ import annotations

import argparse
import json
from pathlib import Path

from metric_lib import mean_overlap, mean_similarity, read_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute replay-stability metrics.")
    parser.add_argument("--results-file", type=Path, required=True)
    parser.add_argument("--fresh-condition", default="loop_centric_fresh")
    parser.add_argument("--replay-condition", default="simple_dag_replay_selective_recompute")
    parser.add_argument("--fresh-graph-condition", default="simple_dag_fresh_recompute")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = read_json(args.results_file)
    loop_fresh = results["conditions"][args.fresh_condition]["runs"]
    fresh_graph = results["conditions"][args.fresh_graph_condition]["runs"]
    replay_runs = results["conditions"][args.replay_condition]["runs"]

    final_outputs = [run.get("final_output", "") for run in replay_runs]
    artifact_sets = [tuple(sorted(run.get("artifact_hashes", {}).items())) for run in replay_runs]
    baseline_artifacts = artifact_sets[0] if artifact_sets else tuple()

    payload = {
        "exact_artifact_hash_match_rate": (
            sum(1 for item in artifact_sets if item == baseline_artifacts) / len(artifact_sets)
            if artifact_sets
            else 0.0
        ),
        "final_output_exact_match_rate": (
            sum(1 for text in final_outputs if text == final_outputs[0]) / len(final_outputs)
            if final_outputs
            else 0.0
        ),
        "distinct_output_count": len(set(final_outputs)),
        "fresh_run_surface_similarity": mean_similarity([item.get("final_output", "") for item in loop_fresh]),
        "fresh_run_semantic_claim_overlap": mean_overlap([item.get("final_output", "") for item in fresh_graph]),
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
