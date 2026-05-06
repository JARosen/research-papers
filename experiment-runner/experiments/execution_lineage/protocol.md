# Protocol

## Thesis

The experiment tests the following hypothesis:

> When downstream generation depends on selected evolving intermediate
> artifacts, explicit DAG-curated context should produce more faithful, stable,
> and update-correct outputs than prompt-centric workflows whose effective
> context is reconstructed implicitly from transcripts, prior outputs, or
> manually supplied notes.

This is an execution-lineage experiment with a strong context-discipline
component, not primarily a better-prose experiment.

## Research Questions

### RQ1: Replay Stability

Under unchanged inputs and execution identities, does an execution-graph
workflow reproduce prior artifacts exactly, while prompt-centric reruns exhibit
fresh-generation variation?

Purpose:

- test deterministic replay as a system property
- do not conflate replay with fresh recomputation
- do not claim that fresh graph executions are inherently less variable

Primary measures:

- exact artifact hash match rate
- final-output exact match rate
- distinct artifact count across replayed runs
- step execution source: fresh vs cached/replayed

Diagnostic measures only:

- fresh-run surface similarity
- fresh-run semantic claim overlap

### RQ2: Update Locality

After controlled upstream edits, does an execution-graph workflow localize
recomputation to affected stages, preserve unaffected artifacts, and reduce
unrelated output churn relative to prompt-centric workflows?

Measures:

- stages recomputed count / percent
- artifacts preserved count / percent
- unrelated output churn
- changed-claim precision
- changed-claim recall
- unaffected-claim regression rate
- wall-clock update time
- model calls / tokens after edit, when available
- manual context reconstruction actions

### RQ3: Context Discipline and Output Faithfulness

When downstream generation depends on selected evolving intermediate artifacts,
does an execution-graph workflow reduce stale-context usage,
irrelevant-context contamination, unsupported claim drift, and improve
claim-level traceability relative to prompt-centric workflows?

Measures:

- stale-context usage
- irrelevant-source contamination
- unsupported claim rate
- relevant evidence recall
- contradiction / tension preservation
- dependency compliance
- claim-level traceability score
- output-faithfulness score

## Conditions

| ID | Condition | Purpose |
|---|---|---|
| `C1` | `loop_centric_fresh` | Controlled prompt-centric fresh baseline |
| `C2` | `loop_real_world_final_update` | Chat-style prior-memo update baseline |
| `C3` | `loop_real_world_with_notes` | Prior-memo plus pasted-notes update baseline |
| `C4` | `loop_real_world_with_memory` | Transparent memory/wiki update baseline |
| `C5` | `simple_dag_fresh_recompute` | Primary in-repo DAG recomputation baseline |
| `C6` | `simple_dag_replay_selective_recompute` | Primary in-repo DAG replay/update baseline |
| Optional `C10` | `loop_real_world_staged_update` | Stronger staged manual-update baseline |
| Disabled first pass `C7` | `thruwire_fresh_recompute` | Secondary ThruWire fresh recomputation validation |
| Disabled first pass `C8` | `thruwire_replay_selective_recompute` | Secondary ThruWire replay/update validation |
| Optional `C9` | `chatgpt_product_selenium` | Preserved ecological/product baseline |

Rules:

- Do not treat `C1` fresh generation as equivalent to `C6` replay.
- Use `C5` when comparing fresh graph recomputation against fresh prompt runs.
- Report fresh-run variation separately from replay stability.

## Harnesses

The default scientific comparison is:

- same provider/model family under a loop-centric harness
- versus a minimal in-repo execution-lineage DAG harness

The loop-centric harness carries workflow state through prompt text, compact
conversation history, rolling summaries, optional prior notes, and optional
transparent memory retrieval. Its real-world update baselines do not receive
explicit dependency edges, execution identities, replay, automatic
invalidation, expected affected claims, expected recomputed stages, or
allowed/disallowed artifact lists.

The simple DAG harness uses the same model client and prompt assets as the
loop-centric harness, but routes work through explicit stage dependencies,
identity-based replay, and selective recomputation. This is the primary
apples-to-apples substrate comparison.

Harness inventory for the current implementation:

- `loop_centric`: active by default
- `simple_dag`: active by default
- `thruwire`: implemented, disabled in the first-pass default config
- `chatgpt_product_selenium`: implemented, retained for future product-baseline work

The current default model alignment is:

- loop-centric harness direct model: `gpt-5.2`
- simple DAG harness direct model: `gpt-5.2`
- ThruWire sibling executor config: `openai/gpt-5.2`

Judge/evaluator models may differ and may be later.

The procedural-memory baseline stores recipe-as-text and prior artifacts as
transparent markdown memory entries behind `memory.list()` and `memory.get(id)`.
It does not attempt to replicate proprietary product memory.

## Reporting Structure

Although the raw experiment records multiple loop-centric baselines and two DAG
execution modes, the recommended reporting structure is paired:

- `loop_fresh_vs_dag_fresh`
- `loop_real_world_final_update_vs_dag_update`
- `loop_real_world_with_notes_vs_dag_update`
- `loop_real_world_with_memory_vs_dag_update`
- optional `loop_real_world_staged_update_vs_dag_update`

This avoids presenting the experiment as an unhelpful `4 vs 2` condition grid
while preserving the stronger challenge baselines.

## Workflow Graph

```text
Utilization Context ----\
Reimbursement Context ---+--> Claim Matrix -----------\
Operations Context ------/                           |
                                                    +--> Recommendation Criteria --> Final Memo
Access vs Cost Context --> Tension / Risk Analysis -/
```

The DAG-side final memo should depend only on declared current artifacts, especially:

- `claim_matrix.current`
- `tension_analysis.current`
- `recommendation_criteria.current`

It should not receive the full raw transcript or all raw sources unless the
condition explicitly tests that behavior.

## Controlled Hazards

Each task should include:

1. irrelevant-source contamination hazard
2. superseded-context usage hazard
3. conflicting evidence / tension preservation hazard
4. localized upstream edit hazard
5. unrelated branch hazard

## Judge Protocol

The judge must be blinded to system identity.

Judge bundles must:

- randomize per-task system labels such as `System A`, `System B`, `System C`
- omit condition ids from the judge-facing bundle
- preserve a private mapping file outside the judge bundle
- ask claim-level questions rather than global preference questions
- support `output_only` and `traceability` modes without mixing them

The judge should evaluate:

- source grounding
- stale-context use
- irrelevant-context contamination
- changed-claim correctness after edits
- regression on unaffected claims
- traceability from final claims to sources and intermediates

## Result Handling

- No fabricated results
- No claims about completed measurements before a run exists
- Paper source files remain untouched until experiment data is collected
