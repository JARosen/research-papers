from __future__ import annotations

from pathlib import Path

from experiment_runner.config import EXPERIMENTS_DIR


PROMPTS_DIR = EXPERIMENTS_DIR / "prompts"


def prompt_path(name: str) -> Path:
    return PROMPTS_DIR / name


def load_prompt(name: str) -> str:
    return prompt_path(name).read_text().strip()
