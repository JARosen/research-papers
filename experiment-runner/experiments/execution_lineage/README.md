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
- `C2` `loop_centric_update_final_only`
- `C3` `loop_centric_update_with_intermediates`
- `C4` `loop_centric_with_procedural_memory`
- `C5` `thruwire_fresh_recompute`
- `C6` `thruwire_replay_selective_recompute`
- Optional `C7` `chatgpt_product_selenium`

The primary comparison is now:

- loop-centric / prompt-centric execution
- versus execution-graph lineage

The optional Selenium harness is preserved only for future ecological/product
comparisons.

Default model alignment:

- loop-centric harness: `gpt-5.2`
- ThruWire sibling executor: `openai/gpt-5.2`
- judge: may use any suitable later model

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

The active execution path is a matched six-stage workflow in both harnesses:
`source_set -> evidence_digest -> claim_matrix -> tension_analysis -> recommendation_criteria -> final_memo`.

The schema supports adding more task families later without changing the judge
or scoring contract.
