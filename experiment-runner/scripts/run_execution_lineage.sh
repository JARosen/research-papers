#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TASK_FILE="${TASK_FILE:-experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1/task.json}"
REPEATS="${REPEATS:-3}"
OUTPUT_DIR="${OUTPUT_DIR:-results/execution-lineage-run}"

export EXPERIMENT_MODEL_PROVIDER="${EXPERIMENT_MODEL_PROVIDER:-openai}"
export EXPERIMENT_MODEL="${EXPERIMENT_MODEL:-gpt-5.2}"
export RUN_OPENAI_EVALUATION="${RUN_OPENAI_EVALUATION:-0}"

.venv/bin/python scripts/preflight_env.py

.venv/bin/python -m experiment_runner.cli run \
  --task-file "$TASK_FILE" \
  --repeats "$REPEATS" \
  --output-dir "$OUTPUT_DIR"
