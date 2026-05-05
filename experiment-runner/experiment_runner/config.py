from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


REPO_DIR = Path(__file__).resolve().parents[1]
PAPER_REPO_DIR = REPO_DIR.parent
GITHUB_DIR = PAPER_REPO_DIR.parent
VERIFICATION_REPO_DIR = GITHUB_DIR / "verification"


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
    chatgpt_url: str
    chatgpt_profile_dir: Path
    chatgpt_profile_name: Optional[str]
    chatgpt_timeout_s: float
    thruwire_model_provider: str
    keep_thruwire_project: bool
    chatgpt_browser_channel: str
    chatgpt_cdp_url: str

    @classmethod
    def from_env(cls) -> "RunnerConfig":
        load_environment()
        profile_path = Path(
            os.getenv(
                "CHATGPT_CHROME_PROFILE_PATH",
                "/Users/dev/Library/Application Support/Google/Chrome/Profile 4",
            )
        )
        return cls(
            chatgpt_url=os.getenv("CHATGPT_URL", "https://chatgpt.com/"),
            chatgpt_profile_dir=profile_path.parent,
            chatgpt_profile_name=profile_path.name,
            chatgpt_timeout_s=float(os.getenv("CHATGPT_TIMEOUT_S", "240")),
            thruwire_model_provider=os.getenv("THRUWIRE_EXPERIMENT_MODEL_PROVIDER", "openai"),
            keep_thruwire_project=os.getenv("THRUWIRE_KEEP_PROJECT", "").lower() in {"1", "true", "yes"},
            chatgpt_browser_channel=os.getenv("CHATGPT_BROWSER_CHANNEL", "chrome"),
            chatgpt_cdp_url=os.getenv("CHATGPT_CDP_URL", "http://127.0.0.1:9222"),
        )
