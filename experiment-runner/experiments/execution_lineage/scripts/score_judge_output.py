from __future__ import annotations

import argparse
from pathlib import Path

from metric_lib import mean_overlap, mean_similarity, read_json, write_json
from compute_context_discipline import _claim_counts, _tension_counts
from compute_update_locality import changed_claim_metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate RQ metrics from results and judge output.")
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--results-file", type=Path, required=True)
    parser.add_argument("--judge-output", type=Path, required=True)
    parser.add_argument("--mapping-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ground_truth = read_json(args.task_dir / "ground_truth.json")
    results = read_json(args.results_file)
    judge_output = read_json(args.judge_output)
    mapping = read_json(args.mapping_file)

    loop_fresh = results["conditions"]["loop_centric_fresh"]["runs"]
    thru_fresh = results["conditions"]["thruwire_fresh_recompute"]["runs"]
    thru_replay = results["conditions"]["thruwire_replay_selective_recompute"]["runs"]
    replay_outputs = [item.get("final_output", "") for item in thru_replay]
    artifact_sets = [tuple(sorted(item.get("artifact_hashes", {}).items())) for item in thru_replay]

    anonymous_id = next(key for key, value in mapping.items() if value == "thruwire_replay_selective_recompute")
    judged = next(item for item in judge_output["systems"] if item["anonymous_id"] == anonymous_id)
    precision, recall, regression = changed_claim_metrics(judged.get("claims", []), ground_truth)
    updated = results["conditions"]["thruwire_replay_selective_recompute"].get("updated_run", {})
    claim_counts = _claim_counts(judged)
    tension_counts = _tension_counts(judged)

    rq_metrics = {
        "RQ1": {
            "exact_artifact_hash_match_rate": (
                sum(1 for item in artifact_sets if item == artifact_sets[0]) / len(artifact_sets)
                if artifact_sets
                else 0.0
            ),
            "final_output_exact_match_rate": (
                sum(1 for text in replay_outputs if text == replay_outputs[0]) / len(replay_outputs)
                if replay_outputs
                else 0.0
            ),
            "distinct_output_count": len(set(replay_outputs)),
            "fresh_run_surface_similarity": mean_similarity([item.get("final_output", "") for item in loop_fresh]),
            "fresh_run_semantic_claim_overlap": mean_overlap([item.get("final_output", "") for item in thru_fresh]),
        },
        "RQ2": {
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
        },
        "RQ3": {
            "claim_count": claim_counts["claim_count"],
            "unsupported_claim_count": claim_counts["unsupported_claim_count"],
            "irrelevant_context_claim_count": claim_counts["irrelevant_context_claim_count"],
            "stale_context_claim_count": claim_counts["stale_context_claim_count"],
            "traceable_claim_count": claim_counts["traceable_claim_count"],
            "reused_vs_recomputed_identifiable_count": claim_counts["reused_vs_recomputed_identifiable_count"],
            "required_tension_count": tension_counts["required_tension_count"],
            "preserved_tension_count": tension_counts["preserved_tension_count"],
            "unsupported_claim_rate": judged["summary_scores"].get("unsupported_claim_rate"),
            "irrelevant_context_contamination_rate": judged["summary_scores"].get("irrelevant_context_contamination_rate"),
            "stale_context_usage_rate": judged["summary_scores"].get("stale_context_usage_rate"),
            "evidence_recall_score": judged["summary_scores"].get("evidence_recall_score"),
            "required_tension_preservation_score": judged["summary_scores"].get("tension_preservation_score"),
            "dependency_compliance_score": judged["summary_scores"].get("dependency_compliance_score"),
            "claim_level_traceability_score": judged["summary_scores"].get("traceability_score"),
            "output_faithfulness_score": judged["summary_scores"].get("output_faithfulness_score"),
            "memory_specific_diagnostics": {
                "memory_entries_retrieved_count": judged["summary_scores"].get("memory_entries_retrieved_count"),
                "stale_memory_retrieval_rate": judged["summary_scores"].get("stale_memory_retrieval_rate"),
                "irrelevant_memory_retrieval_rate": judged["summary_scores"].get("irrelevant_memory_retrieval_rate"),
                "current_state_precision": judged["summary_scores"].get("current_state_precision"),
                "invalid_downstream_reuse_rate": judged["summary_scores"].get("invalid_downstream_reuse_rate"),
            },
        },
    }
    write_json(args.output_file, rq_metrics)


if __name__ == "__main__":
    main()
