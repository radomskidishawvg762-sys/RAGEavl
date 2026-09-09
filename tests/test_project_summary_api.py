"""Phase 1B G5 — GET /api/projects/{id} workspace summary contract tests.

Fake repositories + dependency_overrides (existing convention). The real
PostgreSQL path is covered by tests/test_phase1a_integration.py. Statistics
sources: dataset/run counts + latest run from ProjectStatsRepository, quality
gate from the SAME QualityGateService.evaluate() the /quality-gate endpoint
uses (never recomputed here).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_project_service, get_project_workspace_service
from app.main import app
from app.models import Project


def _project(pid: str, name: str = "finance") -> Project:
    p = Project(name=name, domain="general", status="active")
    p.id = pid
    p.created_at = datetime.now(UTC)
    return p


class FakeProjectGetRepo:
    def __init__(self, projects: list[Project]) -> None:
        self.projects = {p.id: p for p in projects}

    def get(self, project_id: str):
        return self.projects.get(project_id)


class FakeStatsRepo:
    def __init__(self, *, dataset_count=0, run_count=0, latest_run=None) -> None:
        self.dataset_count = dataset_count
        self.run_count = run_count
        self.latest_run = latest_run
        self.calls: list[str] = []

    def summary(self, project_id: str) -> dict:
        self.calls.append(project_id)
        return {
            "dataset_count": self.dataset_count,
            "run_count": self.run_count,
            "latest_run": self.latest_run,
        }


class FakeGateService:
    def __init__(self, result: dict | None) -> None:
        self.result = result
        self.calls: list[str] = []

    def evaluate(self, run_id: str) -> dict:
        self.calls.append(run_id)
        return self.result


def _latest_run(run_id="run-9", status="completed", **over):
    base = {
        "run_id": run_id, "status": status, "dataset_id": "ds1",
        "dataset_name": "finance-golden", "dataset_version": "v1",
        "overall_score": 0.87, "total_records": 100, "evaluated_records": 100,
        "error_records": 0, "evaluation_coverage": 1.0,
        "created_at": datetime.now(UTC) - timedelta(hours=1), "finished_at": datetime.now(UTC),
    }
    base.update(over)
    return base


def _wire(project=None, stats=None, gate=None) -> None:
    from app.services.project_workspace_service import ProjectWorkspaceService

    if project is not None:
        app.dependency_overrides[get_project_service] = lambda: None  # list svc unused here
    app.dependency_overrides[get_project_workspace_service] = lambda: ProjectWorkspaceService(
        project, stats, gate
    )


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app)


def test_1_summary_shape_with_latest_run_and_gate() -> None:
    _wire(
        FakeProjectGetRepo([_project("p1")]),
        FakeStatsRepo(dataset_count=2, run_count=5, latest_run=_latest_run()),
        FakeGateService({"run_id": "run-9", "status": "PASS", "reasons": [],
                         "metrics": [], "overall_score": 0.87,
                         "evaluated_at": datetime.now(UTC)}),
    )
    resp = _client().get("/api/projects/p1")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"project", "dataset_count", "run_count",
                         "latest_run", "latest_quality_gate"}
    assert body["project"]["id"] == "p1"
    assert body["dataset_count"] == 2 and body["run_count"] == 5
    run = body["latest_run"]
    assert run["run_id"] == "run-9" and run["dataset_name"] == "finance-golden"
    assert run["dataset_version"] == "v1" and run["overall_score"] == 0.87
    assert body["latest_quality_gate"]["status"] == "PASS"


def test_2_gate_evaluated_against_latest_run_id() -> None:
    gate = FakeGateService(None)
    _wire(FakeProjectGetRepo([_project("p1")]),
          FakeStatsRepo(run_count=1, latest_run=_latest_run(run_id="run-x")), gate)
    _client().get("/api/projects/p1")
    assert gate.calls == ["run-x"]  # same evaluate() as /quality-gate endpoint


def test_3_no_runs_empty_summary_no_fake_gate() -> None:
    _wire(FakeProjectGetRepo([_project("p1")]), FakeStatsRepo(), FakeGateService(None))
    body = _client().get("/api/projects/p1").json()
    assert body["dataset_count"] == 0 and body["run_count"] == 0
    assert body["latest_run"] is None
    assert body["latest_quality_gate"] is None


def test_4_non_terminal_latest_run_skips_gate() -> None:
    gate = FakeGateService({"status": "PASS"})
    _wire(FakeProjectGetRepo([_project("p1")]),
          FakeStatsRepo(run_count=1, latest_run=_latest_run(status="running")), gate)
    body = _client().get("/api/projects/p1").json()
    assert gate.calls == []  # no meaningful gate for an in-flight run
    assert body["latest_quality_gate"] is None


def test_5_failed_latest_run_skips_gate() -> None:
    gate = FakeGateService({"status": "PASS"})
    _wire(FakeProjectGetRepo([_project("p1")]),
          FakeStatsRepo(run_count=1, latest_run=_latest_run(status="failed")), gate)
    body = _client().get("/api/projects/p1").json()
    assert gate.calls == []
    assert body["latest_quality_gate"] is None


def test_6_unknown_project_404() -> None:
    _wire(FakeProjectGetRepo([]), FakeStatsRepo(), FakeGateService(None))
    resp = _client().get("/api/projects/nope")
    assert resp.status_code == 404
    assert resp.json()["code"] == "BIZ_NOT_FOUND"


def test_7_service_uses_repo_get_not_exists_flag() -> None:
    """Composition: the real service delegates identity to the project repo."""
    from app.core.errors import NotFoundError as _NFE
    from app.services.project_workspace_service import ProjectWorkspaceService

    class _StrictRepo:
        def get(self, project_id: str):
            raise _NFE(f"project {project_id} not found")

    svc = ProjectWorkspaceService(_StrictRepo(), FakeStatsRepo(), FakeGateService(None))
    with pytest.raises(_NFE):
        svc.summary("whatever")


def test_8_stats_repo_is_the_only_run_deriving_module() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    # frozen invariant: projects resource stays run-free
    for rel in ("app/repositories/project.py", "app/services/project_service.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "EvaluationRun" not in src and "evaluation_runs" not in src, rel
    # the aggregation lives in exactly one dedicated repository
    stats_src = (root / "app/repositories/project_stats.py").read_text(encoding="utf-8")
    assert "EvaluationRun" in stats_src
