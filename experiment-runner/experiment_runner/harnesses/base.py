from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from experiment_runner.tasks import ContextDisciplineTask, UpstreamEdit


@dataclass
class HarnessRunResult:
    payload: dict[str, Any]


class ExperimentHarnessRunner(Protocol):
    def run_fresh(self, task_bundle: ContextDisciplineTask, *, repeats: int = 1) -> HarnessRunResult:
        ...

    def run_update(
        self,
        task_bundle: ContextDisciplineTask,
        prior_run: HarnessRunResult,
        edit: UpstreamEdit,
        *,
        include_intermediates: bool = False,
        use_procedural_memory: bool = False,
    ) -> HarnessRunResult:
        ...
