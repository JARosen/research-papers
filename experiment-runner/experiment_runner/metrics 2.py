from __future__ import annotations

import itertools
from difflib import SequenceMatcher
from statistics import mean, pstdev
from typing import Any


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def summarize_texts(texts: list[str]) -> dict[str, Any]:
    normalized = [text.strip() for text in texts if text and text.strip()]
    if len(normalized) < 2:
        return {
            "count": len(normalized),
            "distinct_count": len(set(normalized)),
            "mean_pairwise_similarity": 1.0 if normalized else 0.0,
            "pairwise_similarity_stddev": 0.0,
        }
    scores = [similarity(a, b) for a, b in itertools.combinations(normalized, 2)]
    return {
        "count": len(normalized),
        "distinct_count": len(set(normalized)),
        "mean_pairwise_similarity": mean(scores),
        "pairwise_similarity_stddev": pstdev(scores),
    }
