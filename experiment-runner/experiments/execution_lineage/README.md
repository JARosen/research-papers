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

## RQs

1. `RQ1: Replay Stability`
2. `RQ2: Update Locality`
3. `RQ3: Context Discipline and Output Faithfulness`

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

The primary comparison is now:

- real-world loop-centric update execution
- versus a minimal execution-lineage DAG harness

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

## Implemented Task

The initial fully implemented task is:

- `telehealth_policy_context_pressure_v1`

It includes:

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

The staged execution path used by the fresh loop baseline and the DAG harness
is an eight-stage workflow:
`utilization_context -> reimbursement_context -> operations_context -> access_cost_context -> claim_matrix -> tension_analysis -> recommendation_criteria -> final_memo`.

The schema supports adding more task families later without changing the judge
or scoring contract.
