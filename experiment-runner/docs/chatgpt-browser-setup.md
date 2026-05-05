# ChatGPT Browser Setup

This experiment uses Selenium to attach to an already running Chrome instance
over the remote debugging port rather than launching Chrome directly.

This is necessary because:

- launching Chrome through browser automation changes browser startup flags
- those flags can interfere with an existing authenticated ChatGPT session
- Chrome refuses remote debugging on the default user data directory

## One-time preparation

Create a cloned Chrome user data directory for the experiment:

```bash
mkdir -p "/Users/dev/Documents/GitHub/research-papers/experiment-runner/.chrome-user-data"
rsync -a --delete \
  "/Users/dev/Library/Application Support/Google/Chrome/" \
  "/Users/dev/Documents/GitHub/research-papers/experiment-runner/.chrome-user-data/"
```

Important:

- fully quit normal Chrome before running `rsync`
- the cloned profile should contain the ChatGPT login you want to reuse

## Start Chrome for the experiment

Launch Chrome from the cloned user data directory with remote debugging enabled:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="/Users/dev/Documents/GitHub/research-papers/experiment-runner/.chrome-user-data" \
  --profile-directory="Profile 4"
```

## Verify remote debugging is available

Run:

```bash
curl http://127.0.0.1:9222/json/version
```

If the browser is attachable, this returns JSON with fields such as:

- `Browser`
- `Protocol-Version`
- `webSocketDebuggerUrl`

## Run the experiment

After Chrome is open and `curl` succeeds:

```bash
cd /Users/dev/Documents/GitHub/research-papers/experiment-runner
. .venv/bin/activate
python -m experiment_runner.cli run --task-file tasks/sample_task.json --repeats 5 --output-dir results/run1 --arms both
```

Optional sanity check before a full run:

```bash
python -m experiment_runner.cli bootstrap-chatgpt
```

## Repeat in the future

For future runs:

1. Quit normal Chrome.
2. If you want a fresh copy of your logged-in profile state, rerun the `rsync`.
3. Launch Chrome with the remote-debugging command above.
4. Confirm `curl http://127.0.0.1:9222/json/version` works.
5. Run the experiment CLI.

## Notes

- Keep the Chrome window open while the experiment is running.
- The runner will try to select `Temporary Chat` for each new conversation.
- If Selenium cannot find `New chat` or `Temporary`, the runner pauses and
  asks you to do that step manually in the browser, then continues.
- If ChatGPT logs out inside the cloned profile, log back in manually in that cloned browser and reuse it.
- After the raw run completes, the runner can perform a final OpenAI evaluation
  pass over the full bundle if `OPENAI_API_KEY` is available in `experiment-runner/.env`.
