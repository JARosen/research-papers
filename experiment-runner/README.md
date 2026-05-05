# Experiment Runner

This subdirectory contains a lightweight experiment runner for the paper's
comparison between:

- a traditional chat-style harness with product-level memory behavior
- ThruWire's explicit execution graph runtime

## What it does

- Runs repeated baseline trials against ChatGPT via Playwright
- Runs repeated graph trials against ThruWire via the sibling `verification` repo
- Applies one controlled upstream edit
- Re-runs both conditions
- Saves raw outputs and a small derived metrics summary

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -e .
python -m playwright install chromium
```

3. Ensure `.env` exists in this directory.

This runner is designed to reuse the same env values as the sibling
`verification` repo. If the local `.env` is missing, the code also falls back to
`../verification/.env`.

## ChatGPT authentication

Bootstrap an authenticated persistent browser profile once:

```bash
python -m experiment_runner.cli bootstrap-chatgpt
```

This opens a persistent Playwright Chromium profile under `.auth/`. Log in
manually, solve any challenge, then return to the terminal and press Enter.

## Run the experiment

```bash
python -m experiment_runner.cli run \
  --task-file tasks/sample_task.json \
  --repeats 5 \
  --output-dir results/sample-run
```

## Notes

- The ChatGPT side is intentionally brittle and UI-dependent.
- The ThruWire side uses the API patterns already present in the sibling
  `verification` harness.
- This runner is meant for collecting paper data, not for producing a stable
  test suite.
