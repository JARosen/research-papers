# Experiment Runner

This runner now targets the ThruWire execution-lineage experiment rather than a
generic prose-comparison study.

The paper source remains out of scope. Experiment assets live under
[`experiment-runner/experiments/execution_lineage`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiments/execution_lineage).

## Research Questions

1. `RQ1: Replay Stability`
   Under unchanged inputs and execution identities, does ThruWire replay prior
   artifacts exactly while prompt-centric reruns exhibit fresh-generation
   variation?
2. `RQ2: Update Locality`
   After a controlled upstream edit, does ThruWire localize recomputation,
   preserve unaffected artifacts, and reduce unrelated churn relative to
   prompt-centric update workflows?
3. `RQ3: Context Discipline and Output Faithfulness`
   When downstream generation depends on selected evolving intermediate
   artifacts, does explicit DAG-curated context reduce stale-context usage,
   irrelevant-context contamination, unsupported claim drift, and improve
   claim-level traceability?

`C6` replay is not equivalent to fresh generation and should never be analyzed
as if it were interchangeable with `C1` loop-centric fresh execution.

## Conditions

- `C1` `loop_centric_fresh`
- `C2` `loop_real_world_final_update`
- `C3` `loop_real_world_with_notes`
- `C4` `loop_real_world_with_memory`
- `C5` `simple_dag_fresh_recompute`
- `C6` `simple_dag_replay_selective_recompute`
- Optional `C10` `loop_real_world_staged_update`
- Optional `C9` `chatgpt_product_selenium`
- Disabled for first pass: `C7` `thruwire_fresh_recompute`
- Disabled for first pass: `C8` `thruwire_replay_selective_recompute`

The Selenium-based ChatGPT product baseline is retained for future
ecological/product-baseline experiments, but it is not the primary scientific
baseline because ChatGPT product behavior includes hidden harness, memory,
model-routing, and product-level context variables.

The controlled loop-centric harness and the in-repo simple DAG harness both
default to `gpt-5.2`, matching the ThruWire executor model configured in the
sibling agent-framework repo (`openai/gpt-5.2`). The judge may use any
suitable model, including a later one.

## Runners

The repo currently contains four harness runners:

1. `LoopCentricHarnessRunner`
   Path:
   [`experiment_runner/harnesses/loop_centric/runner.py`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiment_runner/harnesses/loop_centric/runner.py)
   Status: active in the default first-pass experiment.

2. `SimpleDAGHarnessRunner`
   Path:
   [`experiment_runner/harnesses/simple_dag/runner.py`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiment_runner/harnesses/simple_dag/runner.py)
   Status: active in the default first-pass experiment.

3. `ThruWireHarnessRunner`
   Path:
   [`experiment_runner/harnesses/thruwire/runner.py`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiment_runner/harnesses/thruwire/runner.py)
   Status: implemented, retained for secondary system-validation runs, disabled in the default first-pass config.

4. `ChatGPTProductSeleniumRunner`
   Path:
   [`experiment_runner/harnesses/chatgpt_selenium/runner.py`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiment_runner/harnesses/chatgpt_selenium/runner.py)
   Status: implemented, retained for future ecological/product comparisons, not part of the primary scientific baseline.

So there are four runner types in code, but only two are currently active by
default:

- `loop_centric`
- `simple_dag`

## Assets

- Protocol:
  [`experiments/execution_lineage/protocol.md`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiments/execution_lineage/protocol.md)
- Experiment README:
  [`experiments/execution_lineage/README.md`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiments/execution_lineage/README.md)
- Implemented task:
  [`experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1/task.json`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1/task.json)
- Schemas:
  [`experiments/execution_lineage/schemas`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiments/execution_lineage/schemas)
- Scoring:
  [`experiments/execution_lineage/scripts`](/Users/dev/Documents/GitHub/research-papers/experiment-runner/experiments/execution_lineage/scripts)

## Setup

```bash
cd /Users/dev/Documents/GitHub/research-papers/experiment-runner
pip install .
```

## Run Structure

The Python runner now defaults to the controlled loop-centric harness plus a
minimal in-repo execution-lineage DAG harness. ThruWire is retained in code but
disabled for the first-pass experiment run. The preserved Selenium path is
optional.

Typical workflow:

```bash
.venv/bin/python scripts/preflight_env.py
./scripts/run_execution_lineage.sh
python experiments/execution_lineage/scripts/build_judge_bundle.py \
  --task-dir experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1 \
  --results-file results/execution-lineage-run/results.json \
  --output-dir results/execution-lineage-run/judge \
  --mode traceability
python experiments/execution_lineage/scripts/run_judge_bundle.py \
  --bundle-file results/execution-lineage-run/judge/judge_bundle_traceability.json \
  --output-file results/execution-lineage-run/judge/judge_output.json
python experiments/execution_lineage/scripts/score_judge_output.py \
  --task-dir experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1 \
  --results-file results/execution-lineage-run/results.json \
  --judge-output results/execution-lineage-run/judge/judge_output.json \
  --mapping-file results/execution-lineage-run/judge/system_mapping_traceability.json \
  --output-file results/execution-lineage-run/rq_metrics.json
```

## Output Expectations

Result bundles should capture, at minimum:

- condition id
- task id
- final output text
- intermediate artifacts when available
- artifact ids and hashes
- execution source per step
- cache/replay status
- recomputed vs preserved stages after edits
- timing and token metadata when available
- transcripts and prompt/response logs for loop-centric runs
- memory retrieval logs for the procedural-memory baseline

## Loop Context Strategy

The loop baselines now separate two modes:

- `loop_centric_fresh`: the controlled staged fresh run used for RQ1
- `loop_real_world_*`: naturalistic update baselines that operate over prior
  outputs, optional prior notes, optional memory, and the full current source
  bundle without explicit dependency metadata

The staged loop machinery still uses compact transcript carry-forward rather
than replaying the full raw transcript into every later step. It now uses:

- compact live conversation history
- a rolling summary once estimated history crosses `4000` tokens
- a transparent markdown memory wiki with `INDEX.md`, `memory.list()`, and `memory.get(id)`
- full prompt logs retained only for audit

The procedural-memory baseline is intentionally file-backed and transparent. It
is not semantic retrieval and it does not use DAG dependency knowledge to
pre-scope artifacts. Hidden judge metadata such as affected claim IDs,
recomputed-stage expectations, and allowed/disallowed artifact lists is not
rendered into the real-world loop prompts.

The primary scientific comparison is loop-centric execution versus the in-repo
simple DAG harness. ThruWire remains available in code as a secondary
confirmation path, but it is disabled in the default config for the first pass.
It can still be invoked explicitly with:

```bash
cd /Users/dev/Documents/GitHub/research-papers/experiment-runner
.venv/bin/python -m experiment_runner.cli run \
  --task-file experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1/task.json \
  --repeats 3 \
  --output-dir results/execution-lineage-with-thruwire \
  --conditions loop_centric_fresh loop_real_world_final_update loop_real_world_with_notes loop_real_world_with_memory simple_dag_fresh_recompute simple_dag_replay_selective_recompute thruwire_fresh_recompute thruwire_replay_selective_recompute
```

## Comparison Structure

The default experiment is reported as paired comparisons rather than as a flat
`4 vs 2` condition inventory:

- `loop_fresh_vs_dag_fresh`
- `loop_real_world_final_update_vs_dag_update`
- `loop_real_world_with_notes_vs_dag_update`
- `loop_real_world_with_memory_vs_dag_update`

The raw run still records all underlying conditions, but summaries and scoring
are organized around these matchups.

Fresh-run variation under `RQ1` is diagnostic. The primary `RQ1` criterion is
exact replay under unchanged execution identity.

## Reproducible Run

Use the checked-in wrapper:

```bash
cd /Users/dev/Documents/GitHub/research-papers/experiment-runner
chmod +x scripts/run_execution_lineage.sh
./scripts/run_execution_lineage.sh
```

Defaults:

- venv: `.venv`
- model: `gpt-5.2`
- repeats: `3`
- output dir: `results/execution-lineage-run`
- `RUN_OPENAI_EVALUATION=0` unless you override it
