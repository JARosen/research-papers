from __future__ import annotations

from experiment_runner.config import RunnerConfig
from experiment_runner.harnesses.base import HarnessRunResult
from experiment_runner.tasks import ContextDisciplineTask
from experiment_runner.thruwire_backend import ThruWireExperimentRunner


class ThruWireHarnessRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.runner = ThruWireExperimentRunner(config)

    async def run_all(self, task_bundle: ContextDisciplineTask, *, repeats: int) -> dict[str, HarnessRunResult]:
        payload = await self.runner.run_task(task_bundle, repeats)
        return {
            "thruwire_fresh_recompute": HarnessRunResult(payload=payload["thruwire_fresh_recompute"]),
            "thruwire_replay_selective_recompute": HarnessRunResult(payload=payload["thruwire_replay_selective_recompute"]),
        }
