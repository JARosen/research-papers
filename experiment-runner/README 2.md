# Experiment Runner

This subdirectory contains a lightweight experiment runner for the paper's
comparison between:

- a traditional chat-style harness with product-level memory behavior
- ThruWire's explicit execution graph runtime

## What it does

- Runs repeated baseline trials against ChatGPT by attaching to an already running Chrome session
- Runs repeated fresh-chat baseline trials against ChatGPT
- Runs replay-enabled graph trials against ThruWire via the sibling `verification` repo
- Runs fresh-recompute graph trials against ThruWire with cache disabled
- Applies one controlled upstream edit
- Re-runs both conditions
- Saves raw outputs and a derived metrics summary
- Runs a final OpenAI evaluation pass over the full result bundle when `OPENAI_API_KEY` is available

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install .
```

3. Ensure `.env` exists in this directory.

This runner is designed to reuse the same env values as the sibling
`verification` repo. If the local `.env` is missing, the code also falls back to
`../verification/.env`. The local `.env` should contain `OPENAI_API_KEY` for the
final evaluation step.

## Experiment Structure

The runner is organized around the three experiments used in the paper:

1. `Experiment 1: Fresh repeated runs`
   Measures variance under fixed inputs.

2. `Experiment 2: Replay-enabled repeated runs`
   Measures replay stability and latency.

3. `Experiment 3: Upstream edit`
   Measures incremental update cost and traceability.

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

## Outputs

The runner writes:

- `chatgpt_results.json`
- `thruwire_results.json`
- `summary.json`
- `openai_evaluation.json` when OpenAI evaluation succeeds
- `openai_evaluation_error.json` if the OpenAI evaluation step fails

The ThruWire bundle now includes:

- `replay_repeats`
- `fresh_repeats`
- `updated`

The ChatGPT bundle now includes:

- `repeated_fresh_runs`
- `upstream_edit.initial`
- `upstream_edit.updated`

This supports the paper's three main measurement areas:

- repeated-run variance
- replay-enabled determinism
- upstream-edit propagation

## Notes

- The ChatGPT side is intentionally brittle and UI-dependent.
- If Selenium cannot find `New chat` or the `Temporary` pill, the runner pauses
  and asks you to perform that step manually before continuing.
- The ThruWire side uses the API patterns already present in the sibling
  `verification` harness.
- The final OpenAI evaluation step uses `OPENAI_API_KEY` from the environment or
  `experiment-runner/.env`.
- This runner is meant for collecting paper data, not for producing a stable
  test suite.
