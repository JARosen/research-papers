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
| `C2` | `loop_centric_update_final_only` | Ordinary prompt-centric update baseline |
| `C3` | `loop_centric_update_with_intermediates` | Stronger prompt-centric update baseline |
| `C4` | `loop_centric_with_procedural_memory` | Transparent memory/wiki baseline |
| `C5` | `thruwire_fresh_recompute` | Fresh graph recomputation baseline |
| `C6` | `thruwire_replay_selective_recompute` | Execution-lineage behavior under replay and update |
| Optional `C7` | `chatgpt_product_selenium` | Preserved ecological/product baseline |

Rules:

- Do not treat `C1` fresh generation as equivalent to `C6` replay.
- Use `C5` when comparing fresh graph recomputation against fresh prompt runs.
- Report fresh-run variation separately from replay stability.

## Harnesses

The default scientific comparison is:

- same provider/model family under a loop-centric harness
- versus ThruWire under an execution-graph harness

The loop-centric harness carries workflow state through prompt text,
conversation history, and optional transparent memory retrieval. It does not
use executable dependency edges, execution identities, replay, or automatic
invalidation.

The current default model alignment is:

- loop-centric harness direct model: `gpt-5.2`
- ThruWire sibling executor config: `openai/gpt-5.2`

Judge/evaluator models may differ and may be later.

The procedural-memory baseline stores recipe-as-text and prior artifacts as
transparent memory entries. It does not attempt to replicate proprietary
product memory.

## Workflow Graph

```text
Source Set
  -> Evidence Digest
  -> Claim Matrix
  -> Tension / Risk Analysis
  -> Recommendation Criteria
  -> Final Memo
```

The final memo should depend only on declared current artifacts, especially:

- `claim_matrix.current`
- `tension_analysis.current`
- `recommendation_criteria.current`
- `evidence_digest.selected`

It should not receive the full transcript or all raw sources unless the
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
