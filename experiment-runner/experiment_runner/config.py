from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv


def _discover_repo_dir() -> Path:
    module_repo_dir = Path(__file__).resolve().parents[1]
    if (module_repo_dir / "pyproject.toml").exists() and (module_repo_dir / "experiment_runner").exists():
        return module_repo_dir

    cwd = Path.cwd().resolve()
    search_roots = [cwd, *cwd.parents]
    for candidate in search_roots:
        if (candidate / "pyproject.toml").exists() and (candidate / "experiment_runner").exists():
            return candidate

    return module_repo_dir


REPO_DIR = _discover_repo_dir()
PAPER_REPO_DIR = REPO_DIR.parent
GITHUB_DIR = PAPER_REPO_DIR.parent
VERIFICATION_REPO_DIR = GITHUB_DIR / "verification"
EXPERIMENTS_DIR = REPO_DIR / "experiments" / "execution_lineage"
DEFAULT_CONFIG_PATH = EXPERIMENTS_DIR / "config" / "default_conditions.json"


def load_environment() -> None:
    local_env = REPO_DIR / ".env"
    verification_env = VERIFICATION_REPO_DIR / ".env"
    if local_env.exists():
        load_dotenv(local_env, override=False)
    if verification_env.exists():
        load_dotenv(verification_env, override=False)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class RunnerConfig:
    model_provider: str
    model_name: str
    model_temperature: float
    model_seed: Optional[int]
    default_conditions: tuple[str, ...]
    optional_conditions: tuple[str, ...]
    disabled_conditions: tuple[str, ...]
    loop_context_strategy: str
    chatgpt_url: str
    chatgpt_profile_dir: Path
    chatgpt_profile_name: Optional[str]
    chatgpt_timeout_s: float
    thruwire_model_provider: str
    keep_thruwire_project: bool
    chatgpt_browser_channel: str
    chatgpt_cdp_url: str
    openai_eval_model: str
    run_openai_evaluation: bool
    raw_config: dict[str, Any]

    @classmethod
    def from_env(cls) -> "RunnerConfig":
        load_environment()
        file_config = load_default_experiment_config()
        model_config = dict(file_config.get("model", {}))
        profile_path = Path(
            os.getenv(
                "CHATGPT_CHROME_PROFILE_PATH",
                "/Users/dev/Library/Application Support/Google/Chrome/Profile 4",
            )
        )
        configured_seed = os.getenv("EXPERIMENT_MODEL_SEED")
        seed = int(configured_seed) if configured_seed else model_config.get("seed")
        return cls(
            model_provider=os.getenv("EXPERIMENT_MODEL_PROVIDER", str(model_config.get("provider", "openai"))),
            model_name=resolve_model_name(str(model_config.get("name", "gpt-5.2"))),
            model_temperature=float(os.getenv("EXPERIMENT_TEMPERATURE", str(model_config.get("temperature", 0.7)))),
            model_seed=seed if seed is None or isinstance(seed, int) else None,
            default_conditions=tuple(file_config.get("default_conditions", [])),
            optional_conditions=tuple(file_config.get("optional_conditions", [])),
            disabled_conditions=tuple(file_config.get("disabled_conditions", [])),
            loop_context_strategy=os.getenv(
                "EXPERIMENT_CONTEXT_STRATEGY",
                str(file_config.get("context_strategy", "full_transcript")),
            ),
            chatgpt_url=os.getenv("CHATGPT_URL", "https://chatgpt.com/"),
            chatgpt_profile_dir=profile_path.parent,
            chatgpt_profile_name=profile_path.name,
            chatgpt_timeout_s=float(os.getenv("CHATGPT_TIMEOUT_S", "240")),
            thruwire_model_provider=os.getenv("THRUWIRE_EXPERIMENT_MODEL_PROVIDER", "openai"),
            keep_thruwire_project=os.getenv("THRUWIRE_KEEP_PROJECT", "").lower() in {"1", "true", "yes"},
            chatgpt_browser_channel=os.getenv("CHATGPT_BROWSER_CHANNEL", "chrome"),
            chatgpt_cdp_url=os.getenv("CHATGPT_CDP_URL", "http://127.0.0.1:9222"),
            openai_eval_model=os.getenv("OPENAI_EVAL_MODEL", "gpt-5-mini"),
            run_openai_evaluation=os.getenv("RUN_OPENAI_EVALUATION", "1").lower() in {"1", "true", "yes"},
            raw_config=file_config,
        )


def load_default_experiment_config() -> dict[str, Any]:
    if DEFAULT_CONFIG_PATH.exists():
        return json.loads(DEFAULT_CONFIG_PATH.read_text())
        return {
            "default_conditions": [
                "loop_centric_fresh",
                "loop_centric_update_final_only",
                "loop_centric_update_with_intermediates",
                "loop_centric_with_procedural_memory",
                "simple_dag_fresh_recompute",
                "simple_dag_replay_selective_recompute",
            ],
        "optional_conditions": [
            "chatgpt_product_selenium",
        ],
        "disabled_conditions": [
            "thruwire_fresh_recompute",
            "thruwire_replay_selective_recompute",
        ],
        "context_strategy": "full_transcript",
        "model": {
            "provider": "openai",
            "name": "gpt-5.2",
            "temperature": 0.7,
            "seed": None,
        },
    }


def resolve_model_name(value: str) -> str:
    if value.startswith("${EXPERIMENT_MODEL:-") and value.endswith("}"):
        fallback = value[len("${EXPERIMENT_MODEL:-") : -1]
        return os.getenv("EXPERIMENT_MODEL", fallback)
    return value
