# Execution-Lineage Experiment

This directory defines the next version of the ThruWire execution-lineage
experiment. It replaces the earlier simple brief task with context-pressure
tasks that stress stale context, irrelevant context, conflicting evidence, and
localized upstream edits.

## Scope

This experiment is not a prose-polish bakeoff. It evaluates whether a workflow:

- uses the right current context
- avoids stale and irrelevant context
- preserves important tensions
- updates only claims affected by an upstream change
- makes final claims traceable to sources, intermediate artifacts, and execution
  steps

## Experimental Scope

The paper-minimal package evaluates the same telehealth policy domain across
three update settings:

1. `local_supersession_update`
2. `multi_edit_interaction_update`
3. `multi_round_cumulative_update`

The first task is a single clean source supersession. The second adds multiple
interacting edits whose implications must be synthesized jointly. The third
applies edits sequentially across rounds to test cumulative drift versus stable
lineage maintenance.

## Fast DAG Demo

The fast DAG demo is designed for quick mechanism testing, not final paper
statistics. It isolates two DAG-native capabilities:

1. `unrelated_branch_noop_update`: no-op preservation and unrelated branch isolation
2. `intermediate_artifact_edit`: downstream propagation from a maintained artifact edit

Use:

```bash
cd /Users/dev/Documents/GitHub/research-papers/experiment-runner
.venv/bin/python -m experiment_runner.cli run-fast-dag-demo \
  --output-dir results/fast-dag-demo-smoke \
  --repeats 1 \
  --judge-repeats 1
```

Default conditions:

- `loop_real_world_final_update`
- `loop_real_world_with_edit_event`
- `simple_dag_replay_selective_recompute`

The fast demo exists because the clearest DAG advantage is not “better prose.”
It is knowing what should change, what should not change, and preserving exact
artifact identity when appropriate.

## RQs

1. `RQ1: Replay Stability`
2. `RQ2: Update Locality`
3. `RQ3: Context Discipline and Output Faithfulness`

## Conditions

- `C1` `loop_centric_fresh`
- `C2` `loop_real_world_final_update`
- `C11` `loop_real_world_with_edit_event`
- `C3` `loop_real_world_with_notes`
- `C4` `loop_real_world_with_memory`
- `C5` `simple_dag_fresh_recompute`
- `C6` `simple_dag_replay_selective_recompute`
- Optional `C10` `loop_real_world_staged_update`
- Optional `C9` `chatgpt_product_selenium`
- Disabled for first pass: `C7` `thruwire_fresh_recompute`
- Disabled for first pass: `C8` `thruwire_replay_selective_recompute`

The primary comparison is now:

- real-world loop-centric update execution
- versus a minimal execution-lineage DAG harness

This is a system-level comparison, not an equal-prompt comparison.

| Condition | Current sources | Prior memo | Prior notes / memory | Source edit event | Dependency graph | Selective recomputation |
|---|---:|---:|---:|---:|---:|---:|
| `loop_real_world_final_update` | yes | yes | no | no | no | no |
| `loop_real_world_with_edit_event` | yes | yes | no | yes | no | no |
| `loop_real_world_with_notes` | yes | yes | yes | no | no | no |
| `loop_real_world_with_memory` | yes | yes | memory tool | no | no | no |
| `simple_dag_replay_selective_recompute` | scoped/current | yes/artifacts | structured artifacts | yes | yes | yes |

ThruWire is retained in code as a secondary system-validation comparison, but
it is disabled in the default first-pass config.
The optional Selenium harness is preserved only for future ecological/product
comparisons.

Default model alignment:

- loop-centric harness: `gpt-5.2`
- simple DAG harness: `gpt-5.2`
- ThruWire sibling executor: `openai/gpt-5.2`
- judge: may use any suitable later model

## Runner Inventory

There are four harness types in the codebase:

1. `loop_centric`
   Purpose: primary prompt/loop baseline.
   Status: active by default.

2. `simple_dag`
   Purpose: primary apples-to-apples execution-lineage comparison.
   Status: active by default.

3. `thruwire`
   Purpose: secondary validation against the real ThruWire runtime.
   Status: implemented but disabled in the default first-pass config.

4. `chatgpt_product_selenium`
   Purpose: future ecological/product baseline.
   Status: implemented but not part of the primary scientific baseline.

Only the first two are active in the current default experiment.

## Paired Comparisons

The first-pass experiment should be interpreted as four paired comparisons:

- `loop_fresh_vs_dag_fresh`
- `loop_real_world_final_update_vs_dag_update`
- `loop_real_world_with_edit_event_vs_dag_update`
- `loop_real_world_with_notes_vs_dag_update`
- `loop_real_world_with_memory_vs_dag_update`
- optional `loop_real_world_staged_update_vs_dag_update`

This keeps the stronger loop baselines while presenting the results as explicit
head-to-head matchups against the same DAG-side fresh or update behavior.

## Directory Layout

- `tasks/`: task bundles, source bundles, edits, and answer keys
- `prompts/`: loop-centric, ThruWire, and judge prompts
- `schemas/`: task, ground-truth, judge-output, and result schemas
- `scripts/`: judge-bundle construction and RQ scoring
- `results/`: empty placeholder for collected runs only

## Tasks

- `local_supersession_update`
- `multi_edit_interaction_update`
- `multi_round_cumulative_update`

These tasks collectively include:

- 10 source excerpts
- 2 plausible decoys
- 1 superseded source pair
- 1 required evidence tension
- 1 localized upstream edit
- 1 unrelated branch artifact
- transparent procedural memory entries
- a claim-level ground-truth key

The procedural-memory condition uses a markdown memory wiki rooted at
`memory/INDEX.md`. The harness exposes `memory.list()` and `memory.get(id)`
semantics, logs every retrieval decision, and keeps retrieval heuristic rather
than dependency-scoped.

Real-world loop baselines receive prior outputs, optional prior notes, optional
memory access, and the current source bundle in naturalistic prompts. They do
not receive oracle labels such as expected affected claims, expected
recomputed stages, or allowed/disallowed artifact lists. Those remain hidden
judge metadata or DAG-runtime state.

`loop_real_world_with_edit_event` is a source-change-awareness control. It
receives the old/new source replacement event from the task edit input, but no
dependency graph, affected-claim labels, recomputation scopes, or judge
metadata.

The DAG baseline receives execution metadata because that metadata is produced
by the runtime's explicit dependency graph and edit lineage. The DAG condition
is not merely prompted to behave locally; it is given scoped recomputation
through the graph runtime.

## Paper Runner

Use:

```bash
cd /Users/dev/Documents/GitHub/research-papers/experiment-runner
.venv/bin/python -m experiment_runner.cli run-paper-minimal \
  --output-dir results/paper-minimal-run \
  --repeats 5 \
  --judge-repeats 1
```

This is a system-level comparison, not an equal-prompt comparison. The
`loop_real_world_with_edit_event` condition controls for source-change
awareness by exposing only the old/new source replacement event. No generation
condition should receive judge-only metadata such as expected affected claims,
expected unaffected claims, correct final recommendations, or scoring rubrics.

Prompt families are separated on disk so loop prompt edits do not change DAG
prompt rendering:

- `prompts/loop_real_world/`
- `prompts/loop_staged/`
- `prompts/simple_dag/`

The staged execution path used by the fresh loop baseline and the DAG harness
is an eight-stage workflow:
`utilization_context -> reimbursement_context -> operations_context -> access_cost_context -> claim_matrix -> tension_analysis -> recommendation_criteria -> final_memo`.

Any result bundle generated before this prompt-family split should be treated
as a mixed prompt ablation rather than the clean loop-only baseline rewrite.

The schema supports adding more task families later without changing the judge
or scoring contract.

## What The Experiment Does And Does Not Show

This experiment is not designed to prove that DAG execution always produces
better prose than a loop. A strong model with the full current source bundle
can often produce an excellent final memo in a single holistic call.

The central claim is different: DAG execution makes evolving work maintainable.
It provides deterministic replay, explicit update locality, artifact
preservation, dependency-scoped recomputation, and reduced reliance on implicit
context reconstruction.

Therefore the primary metrics are not only final-output quality. We also report
no-op preservation, unrelated churn, stable artifact hash preservation,
downstream propagation, cross-artifact consistency, stale-structure retention,
token usage, and model-call count.
