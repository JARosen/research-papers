from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
VERIFICATION_REPO = ROOT.parent.parent / "verification"
if str(VERIFICATION_REPO) not in sys.path:
    sys.path.insert(0, str(VERIFICATION_REPO))

from experiment_runner.config import RunnerConfig, load_environment
from verification.auth import firebase_login  # type: ignore[import-untyped]
from verification.config import ServiceConfig  # type: ignore[import-untyped]


REQUIRED_ENV_KEYS = [
    "OPENAI_API_KEY",
    "FIREBASE_API_KEY",
    "THRUWIRE_TEST_EMAIL",
    "THRUWIRE_TEST_PASSWORD",
]

OPTIONAL_ENV_KEYS = [
    "BASE_URL",
    "API_BASE_URL",
    "THRUWIRE_TEST_EMAIL_2",
    "THRUWIRE_TEST_PASSWORD_2",
]


def _env_status() -> dict[str, bool]:
    import os

    status: dict[str, bool] = {}
    for key in REQUIRED_ENV_KEYS + OPTIONAL_ENV_KEYS:
        status[key] = bool(os.getenv(key))
    return status


def _import_status() -> dict[str, bool]:
    modules = ["openai", "selenium", "dotenv", "httpx"]
    result: dict[str, bool] = {}
    for module in modules:
        try:
            importlib.import_module(module)
            result[module] = True
        except Exception:
            result[module] = False
    return result


async def _check_thruwire_login() -> dict[str, str]:
    token = await firebase_login()
    service = ServiceConfig.from_env()
    return {
        "firebase_login": "ok",
        "api_base_url": service.api_base_url,
        "token_prefix": token[:12],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight check for the experiment runner environment.")
    parser.add_argument("--check-thruwire-login", action="store_true")
    args = parser.parse_args()

    load_environment()
    cfg = RunnerConfig.from_env()
    payload: dict[str, object] = {
        "venv_python": sys.executable,
        "imports": _import_status(),
        "env": _env_status(),
        "resolved_config": {
            "model_provider": cfg.model_provider,
            "model_name": cfg.model_name,
            "thruwire_model_provider": cfg.thruwire_model_provider,
            "default_conditions": list(cfg.default_conditions),
            "optional_conditions": list(cfg.optional_conditions),
            "context_strategy": cfg.loop_context_strategy,
            "openai_eval_model": cfg.openai_eval_model,
        },
    }
    if args.check_thruwire_login:
        payload["thruwire_login_check"] = asyncio.run(_check_thruwire_login())
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
