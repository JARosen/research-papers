from __future__ import annotations

import argparse
import json
from pathlib import Path

from metric_lib import read_json


def _claim_counts(judged: dict) -> dict:
    claims = judged.get("claims", [])
    return {
        "claim_count": len(claims),
        "unsupported_claim_count": sum(1 for item in claims if item.get("supported") is False),
        "irrelevant_context_claim_count": sum(1 for item in claims if item.get("uses_irrelevant_context") is True),
        "stale_context_claim_count": sum(1 for item in claims if item.get("uses_stale_context") is True),
        "traceable_claim_count": sum(1 for item in claims if item.get("producing_step_identifiable") is True),
        "reused_vs_recomputed_identifiable_count": sum(
            1 for item in claims if item.get("reused_vs_recomputed_identifiable") is True
        ),
    }


def _tension_counts(judged: dict) -> dict:
    tensions = judged.get("required_tensions", [])
    return {
        "required_tension_count": len(tensions),
        "preserved_tension_count": sum(1 for item in tensions if item.get("preserved") is True),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute execution-lineage context and memory diagnostics.")
    parser.add_argument("--judge-output", type=Path, required=True)
    parser.add_argument("--mapping-file", type=Path, required=True)
    parser.add_argument("--condition-id", default="thruwire_replay_selective_recompute")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    judge_output = read_json(args.judge_output)
    mapping = read_json(args.mapping_file)
    anonymous_id = next(key for key, value in mapping.items() if value == args.condition_id)
    judged = next(item for item in judge_output["systems"] if item["anonymous_id"] == anonymous_id)
    summary = judged["summary_scores"]
    claim_counts = _claim_counts(judged)
    tension_counts = _tension_counts(judged)
    payload = {
        **claim_counts,
        **tension_counts,
        "unsupported_claim_rate": summary.get("unsupported_claim_rate"),
        "irrelevant_context_contamination_rate": summary.get("irrelevant_context_contamination_rate"),
        "stale_context_usage_rate": summary.get("stale_context_usage_rate"),
        "evidence_recall_score": summary.get("evidence_recall_score"),
        "required_tension_preservation_score": summary.get("tension_preservation_score"),
        "dependency_compliance_score": summary.get("dependency_compliance_score"),
        "claim_level_traceability_score": summary.get("traceability_score"),
        "output_faithfulness_score": summary.get("output_faithfulness_score"),
        "memory_entries_retrieved_count": summary.get("memory_entries_retrieved_count"),
        "stale_memory_retrieval_rate": summary.get("stale_memory_retrieval_rate"),
        "irrelevant_memory_retrieval_rate": summary.get("irrelevant_memory_retrieval_rate"),
        "current_state_precision": summary.get("current_state_precision"),
        "invalid_downstream_reuse_rate": summary.get("invalid_downstream_reuse_rate"),
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
