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
- `C2` `loop_centric_update_final_only`
- `C3` `loop_centric_update_with_intermediates`
- `C4` `loop_centric_with_procedural_memory`
- `C5` `thruwire_fresh_recompute`
- `C6` `thruwire_replay_selective_recompute`
- Optional `C7` `chatgpt_product_selenium`

The Selenium-based ChatGPT product baseline is retained for future
ecological/product-baseline experiments, but it is not the primary scientific
baseline because ChatGPT product behavior includes hidden harness, memory,
model-routing, and product-level context variables.

The controlled loop-centric harness defaults to `gpt-5.2`, matching the
ThruWire executor model configured in the sibling agent-framework repo
(`openai/gpt-5.2`). The judge may use any suitable model, including a later
one.

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

The Python runner now defaults to the controlled loop-centric harness plus the
ThruWire execution-graph harness. The preserved Selenium path is optional.

Typical workflow:

```bash
.venv/bin/python scripts/preflight_env.py
./scripts/run_execution_lineage.sh
python experiments/execution_lineage/scripts/build_judge_bundle.py \
  --task-dir experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1 \
  --results-file results/execution-lineage-run/results.json \
  --output-dir results/execution-lineage-run/judge \
  --mode output_only
python experiments/execution_lineage/scripts/score_judge_output.py \
  --task-dir experiments/execution_lineage/tasks/telehealth_policy_context_pressure_v1 \
  --results-file results/execution-lineage-run/results.json \
  --judge-output results/execution-lineage-run/judge/judge_output.json \
  --mapping-file results/execution-lineage-run/judge/system_mapping_output_only.json \
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
