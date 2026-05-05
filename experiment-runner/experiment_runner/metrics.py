from __future__ import annotations

import hashlib
import itertools
from difflib import SequenceMatcher
from statistics import mean, pstdev
from typing import Any


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a or "", b or "").ratio()


def sentence_overlap(a: str, b: str) -> float:
    left = {item.strip().lower() for item in (a or "").replace(";", ".").split(".") if item.strip()}
    right = {item.strip().lower() for item in (b or "").replace(";", ".").split(".") if item.strip()}
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def text_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def summarize_texts(texts: list[str]) -> dict[str, Any]:
    normalized = [text.strip() for text in texts if text and text.strip()]
    if len(normalized) < 2:
        return {
            "count": len(normalized),
            "distinct_count": len(set(normalized)),
            "mean_pairwise_similarity": 1.0 if normalized else 0.0,
            "pairwise_similarity_stddev": 0.0,
            "mean_sentence_overlap": 1.0 if normalized else 0.0,
        }
    scores = [similarity(a, b) for a, b in itertools.combinations(normalized, 2)]
    overlaps = [sentence_overlap(a, b) for a, b in itertools.combinations(normalized, 2)]
    return {
        "count": len(normalized),
        "distinct_count": len(set(normalized)),
        "mean_pairwise_similarity": mean(scores),
        "pairwise_similarity_stddev": pstdev(scores),
        "mean_sentence_overlap": mean(overlaps),
    }
