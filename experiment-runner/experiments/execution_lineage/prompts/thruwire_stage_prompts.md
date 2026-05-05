# ThruWire Stage Prompts

`Source Set`
- expose only the active current sources for the run

`Evidence Digest`
- extract relevant evidence items
- explicitly mark decoys as excluded
- note which source ids are current and which are superseded

`Claim Matrix`
- map candidate claims to source ids
- mark claims as required, optional, stale, or excluded
- mark whether each claim is affected by the upstream edit

`Tension / Risk Analysis`
- preserve conflicts and unresolved uncertainty
- do not collapse conflicting evidence into a false certainty

`Recommendation Criteria`
- define the decision criteria that the final memo must satisfy
- keep criteria grounded in current claims and preserved tensions

`Final Memo`
- use only `evidence_digest.selected`, `claim_matrix.current`,
  `tension_analysis.current`, and `recommendation_criteria.current`
- do not use the full transcript, superseded sources, or irrelevant decoys

`Executive Summary`
- summarize the final memo without introducing new claims
