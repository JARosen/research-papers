# Memory Index

Use `memory.get(id)` to read one file only when it may help the current step.
Treat memory as advisory text, not executable dependencies.
Prefer current source bundle over any stale memory content.

- `workflow_recipe_v1`: stable workflow recipe for the loop baseline
- `source_version_notes_v1`: current vs superseded source note
- `prior_claim_matrix_v1`: prior claim matrix summary from an earlier run
- `prior_tension_analysis_v1`: prior tension analysis summary from an earlier run
