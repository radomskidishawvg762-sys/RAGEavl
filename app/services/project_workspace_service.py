"""ProjectWorkspaceService (Phase 1B G5) — project summary aggregation.

Composes read-only sources (service composition follows the existing
RegressionService -> ComparisonService pattern in deps.py):
  ProjectRepository          identity row (404 when unknown)
  ProjectStatsRepository     dataset/run counts + latest run
  QualityGateService         gate of the latest run — the SAME evaluate() the
                             /quality-gate endpoint uses, so numbers cannot
                             drift between surfaces.

Gate is computed only for a terminal quality run (completed /
completed_with_errors): failed/cancelled/in-flight runs have no meaningful
gate, and the summary reports None instead of faking one.
"""

from __future__ import annotations

from app.core.errors import NotFoundError

_GATE_ELIGIBLE_STATUSES = {"completed", "completed_with_errors"}


class ProjectWorkspaceService:
    def __init__(self, project_repo, stats_repo, quality_gate_service) -> None:
        self._projects = project_repo
        self._stats = stats_repo
        self._gate = quality_gate_service

    def summary(self, project_id: str) -> dict:
        project = self._projects.get(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id} not found")
        stats = self._stats.summary(project_id)
        latest_run = stats.get("latest_run")
        quality_gate = None
        if latest_run and latest_run["status"] in _GATE_ELIGIBLE_STATUSES:
            quality_gate = self._gate.evaluate(latest_run["run_id"])
        return {
            "project": project,
            "dataset_count": stats["dataset_count"],
            "run_count": stats["run_count"],
            "latest_run": latest_run,
            "latest_quality_gate": quality_gate,
        }
