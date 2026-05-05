from __future__ import annotations

import hashlib
import json
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Any


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    return " ".join((value or "").split()).strip().lower()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a or "", b or "").ratio()


def sentence_set(text: str) -> set[str]:
    raw = normalize_text(text)
    if not raw:
      return set()
    return {
        sentence.strip()
        for sentence in raw.replace(";", ".").split(".")
        if sentence.strip()
    }


def sentence_overlap(a: str, b: str) -> float:
    left = sentence_set(a)
    right = sentence_set(b)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union)


def pairwise(values: list[str]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for index in range(len(values)):
        for other in range(index + 1, len(values)):
            items.append((values[index], values[other]))
    return items


def mean_similarity(values: list[str]) -> float:
    combos = pairwise(values)
    if not combos:
        return 1.0 if values else 0.0
    return mean(similarity(a, b) for a, b in combos)


def mean_overlap(values: list[str]) -> float:
    combos = pairwise(values)
    if not combos:
        return 1.0 if values else 0.0
    return mean(sentence_overlap(a, b) for a, b in combos)


def claim_ids(ground_truth: dict[str, Any], *, affected: bool | None = None) -> set[str]:
    ids = set()
    for claim in ground_truth.get("expected_claims", []):
        if affected is None or bool(claim.get("affected_by_edit")) is affected:
            ids.add(str(claim.get("id")))
    return ids
