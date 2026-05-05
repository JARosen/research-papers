from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import RunnerConfig, load_environment
from .tasks import ContextDisciplineTask

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


EVALUATION_SCHEMA = {
    "name": "experiment_evaluation",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overview": {"type": "string"},
            "chatgpt_repeated_run_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "major_claims_consistency": {"type": "string"},
                    "evidence_consistency": {"type": "string"},
                    "variance_assessment": {"type": "string"},
                    "notable_substantive_differences": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "major_claims_consistency",
                    "evidence_consistency",
                    "variance_assessment",
                    "notable_substantive_differences",
                ],
            },
            "thruwire_replay_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "output_stability_assessment": {"type": "string"},
                    "replay_behavior_assessment": {"type": "string"},
                    "does_result_support_execution_lineage_claim": {"type": "boolean"},
                },
                "required": [
                    "output_stability_assessment",
                    "replay_behavior_assessment",
                    "does_result_support_execution_lineage_claim",
                ],
            },
            "thruwire_fresh_recompute_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "fresh_run_variance_assessment": {"type": "string"},
                    "timing_assessment": {"type": "string"},
                },
                "required": ["fresh_run_variance_assessment", "timing_assessment"],
            },
            "upstream_edit_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "chatgpt_update_behavior": {"type": "string"},
                    "thruwire_update_behavior": {"type": "string"},
                    "did_thruwire_preserve_unaffected_work": {"type": "string"},
                    "which_claims_changed": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "chatgpt_update_behavior",
                    "thruwire_update_behavior",
                    "did_thruwire_preserve_unaffected_work",
                    "which_claims_changed",
                ],
            },
            "traceability_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "chatgpt_traceability": {"type": "string"},
                    "thruwire_traceability": {"type": "string"},
                    "winner": {"type": "string"},
                },
                "required": ["chatgpt_traceability", "thruwire_traceability", "winner"],
            },
            "paper_ready_findings": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "overview",
            "chatgpt_repeated_run_assessment",
            "thruwire_replay_assessment",
            "thruwire_fresh_recompute_assessment",
            "upstream_edit_assessment",
            "traceability_assessment",
            "paper_ready_findings",
        ],
    },
}


class OpenAIEvaluator:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        load_environment()
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not found in environment or experiment-runner/.env")
        if OpenAI is None:
            raise RuntimeError(
                "openai package is not installed in the active environment. "
                "Run `.venv/bin/pip install -e .` from experiment-runner or `.venv/bin/pip install openai`."
            )
        self.client = OpenAI(api_key=self.api_key)

    def evaluate(
        self,
        *,
        task: ContextDisciplineTask,
        chatgpt_results: dict[str, Any] | None,
        thruwire_results: dict[str, Any] | None,
        summary: dict[str, Any],
        output_dir: Path,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(task, chatgpt_results, thruwire_results, summary)
        print(f"[evaluation] running OpenAI evaluation with model {self.config.openai_eval_model} ...")
        response = self.client.responses.create(
            model=self.config.openai_eval_model,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": EVALUATION_SCHEMA["name"],
                    "schema": EVALUATION_SCHEMA["schema"],
                    "strict": True,
                }
            },
        )
        output_text = getattr(response, "output_text", None)
        if not output_text:
            raise RuntimeError("OpenAI evaluation response did not include output_text.")
        payload = json.loads(output_text)
        output_path = output_dir / "openai_evaluation.json"
        output_path.write_text(json.dumps(payload, indent=2))
        print(f"[evaluation] wrote {output_path}")
        return payload

    def _build_prompt(
        self,
        task: ContextDisciplineTask,
        chatgpt_results: dict[str, Any] | None,
        thruwire_results: dict[str, Any] | None,
        summary: dict[str, Any],
    ) -> str:
        bundle = {
            "task": {
                "task_id": task.task_id,
                "topic": task.title,
                "instructions": task.instruction,
                "sources": [
                    {"id": s.id, "title": s.title, "kind": s.kind, "status": s.status, "content": s.excerpt}
                    for s in task.sources
                ],
                "upstream_edit": {
                    "edit_id": task.primary_edit.edit_id,
                    "description": task.primary_edit.description,
                    "old_source_id": task.primary_edit.old_source_id,
                    "new_source_id": task.primary_edit.new_source_id,
                },
            },
            "chatgpt_results": chatgpt_results,
            "thruwire_results": thruwire_results,
            "summary": summary,
        }
        return (
            "You are evaluating a research-paper experiment comparing two workflow systems for the same "
            "multi-step evidence-synthesis task: a traditional chat harness and a deterministic "
            "execution-graph system.\n\n"
            "This is a workflow evaluation, not a topic evaluation. The task topic is intentionally neutral. "
            "Judge the systems on workflow behavior, not on whether their outputs endorse any particular "
            "thesis.\n\n"
            "The bundle contains three experiment types:\n"
            "1. Experiment 1: fresh repeated runs under fixed inputs.\n"
            "2. Experiment 2: replay-enabled repeated runs for the execution-graph system.\n"
            "3. Experiment 3: an upstream source edit followed by an updated run.\n\n"
            "Use these definitions consistently:\n"
            "- Substantive difference: a change in claims, evidence cited, interpretation of evidence, or "
            "bottom-line conclusions.\n"
            "- Stylistic difference: a change in wording, order, tone, formatting, or verbosity that does not "
            "materially alter claims or evidence use.\n"
            "- Traceability: how clearly a reader can identify which source or intermediate step supports a "
            "claim, and after the upstream edit, which parts of the workflow changed versus stayed stable.\n"
            "- Replay: intended reuse of previously computed intermediate results when inputs have not changed. "
            "Do not treat replay itself as suspicious or unfair; it is the system property being evaluated.\n"
            "- Fresh recomputation: a new run without reuse of prior intermediate results.\n\n"
            "What to measure:\n"
            "- For Experiment 1, assess substantive variance across repeated fresh runs. Do not overclaim from "
            "string-level or stylistic variation alone.\n"
            "- For Experiment 2, assess whether replay-enabled runs are stable and whether the reported replay "
            "or execution-source behavior supports the paper's determinism and lineage claims.\n"
            "- For Experiment 3, assess whether the revised source propagates into changed claims or evidence, "
            "whether unaffected material appears preserved, and what the update cost suggests about each system.\n\n"
            "Fairness requirements:\n"
            "- Compare ChatGPT fresh temporary chats against ThruWire replay-enabled runs only for the specific "
            "claim that replay changes repeat-run behavior. Do not describe that difference as a confound; it is "
            "the intended contrast in Experiment 2.\n"
            "- When considering Experiment 1 variance, compare fresh ChatGPT runs to fresh-recompute ThruWire "
            "runs, not to replay-enabled runs.\n"
            "- Do not infer factual correctness beyond the provided bundle. Judge internal consistency, "
            "responsiveness to the source revision, and evidentiary traceability only from the materials given.\n"
            "- Be careful not to equate identical wording with better reasoning, and do not equate wording "
            "differences with substantive disagreement unless the claims or evidence actually differ.\n\n"
            "Output requirements:\n"
            "- Return only JSON matching the schema.\n"
            "- Make the assessments paper-ready, concrete, and evidence-based.\n"
            "- In paper_ready_findings, write concise findings that could plausibly appear in the paper without "
            "overstating what this bundle proves.\n\n"
            "Use the bundle below and return a structured JSON assessment.\n\n"
            + json.dumps(bundle, indent=2)
        )
