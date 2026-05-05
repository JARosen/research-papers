from __future__ import annotations

from pathlib import Path

from experiment_runner.config import EXPERIMENTS_DIR


PROMPTS_DIR = EXPERIMENTS_DIR / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text().strip()
