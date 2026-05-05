# Experiment Runner

This subdirectory contains a lightweight experiment runner for the paper's
comparison between:

- a traditional chat-style harness with product-level memory behavior
- ThruWire's explicit execution graph runtime

## What it does

- Runs repeated baseline trials against ChatGPT by attaching to an already running Chrome session
- Runs repeated graph trials against ThruWire via the sibling `verification` repo
- Applies one controlled upstream edit
- Re-runs both conditions
- Saves raw outputs and a small derived metrics summary

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install .
```

3. Ensure `.env` exists in this directory.

This runner is designed to reuse the same env values as the sibling
`verification` repo. If the local `.env` is missing, the code also falls back to
`../verification/.env`.

## ChatGPT authentication

Do not launch Chrome through Playwright directly for ChatGPT authentication.
Instead, start a cloned Chrome profile with remote debugging enabled and let
the runner attach to it.

See:

- [docs/chatgpt-browser-setup.md](/Users/dev/Documents/GitHub/research-papers/experiment-runner/docs/chatgpt-browser-setup.md)

To verify the browser is attachable:

```bash
curl http://127.0.0.1:9222/json/version
```

Optional sanity check before a full run:

```bash
python -m experiment_runner.cli bootstrap-chatgpt
```

This attaches to the already-running Chrome session and opens ChatGPT so you can
confirm the logged-in state manually.

## Run the experiment

```bash
python -m experiment_runner.cli run \
  --task-file tasks/sample_task.json \
  --repeats 5 \
  --output-dir results/sample-run \
  --arms both
```

## Notes

- The ChatGPT side is intentionally brittle and UI-dependent.
- If Selenium cannot find `New chat` or the `Temporary` pill, the runner pauses
  and asks you to perform that step manually before continuing.
- The ThruWire side uses the API patterns already present in the sibling
  `verification` harness.
- This runner is meant for collecting paper data, not for producing a stable
  test suite.
