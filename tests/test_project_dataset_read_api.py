"""T-16A read API tests — GET /api/projects and GET /api/datasets.

API -> Service -> Repository chain is exercised through dependency_overrides
with fake repositories (no DB, no network — TEST_DATABASE_URL isolation kept).
Architecture invariants (thin router, resources never derived from runs) are
checked via source reading, matching the existing test_evaluation_api pattern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_dataset_service, get_project_service
from app.main import app
from app.models import Dataset, Project
from app.services.dataset_service import DatasetService
from app.services.project_service import ProjectService

ROOT = Path(__file__).resolve().parents[1]


def _utc(days_ago: int = 0) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_ago)


# ---------------- fake repositories (in-memory list_page mirrors) ----------------


class FakeProjectRepo:
    def __init__(self, projects: list[Project] | None = None) -> None:
        self.projects = projects or []

    def get(self, project_id: str):
        return next((p for p in self.projects if p.id == project_id), None)

    def list_page(self, *, page, page_size, sort="created_at", order="desc", status=None):
        cols = {"id": "id", "name": "name", "domain": "domain",
                "status": "status", "created_at": "created_at"}
        if cols.get(sort) is None or order not in {"asc", "desc"}:
            raise ValueError("unsupported project sort or order")
        rows = [p for p in self.projects if status is None or p.status == status]
        rows = sorted(rows, key=lambda p: getattr(p, cols[sort]), reverse=(order == "desc"))
        start = (page - 1) * page_size
        return rows[start : start + page_size], len(rows)


class FakeDatasetRepo:
    def __init__(self, datasets: list[Dataset] | None = None) -> None:
        self.datasets = datasets or []

    def get(self, dataset_id: str):
        return next((d for d in self.datasets if d.id == dataset_id), None)

    def list_page(self, *, page, page_size, project_id=None, sort="created_at", order="desc"):
        cols = {"id": "id", "project_id": "project_id", "name": "name", "version": "version",
                "record_count": "record_count", "validation_status": "validation_status",
                "is_locked": "is_locked", "created_at": "created_at"}
        if cols.get(sort) is None or order not in {"asc", "desc"}:
            raise ValueError("unsupported dataset sort or order")
        rows = [d for d in self.datasets if project_id is None or d.project_id == project_id]
        rows = sorted(rows, key=lambda d: getattr(d, cols[sort]), reverse=(order == "desc"))
        start = (page - 1) * page_size
        return rows[start : start + page_size], len(rows)


# ---------------- helpers ----------------


def _make_project(name: str, *, domain="general", status="active", created_at=None) -> Project:
    p = Project(name=name, domain=domain, status=status)
    p.id = str(uuid.uuid4())
    p.created_at = created_at or _utc()
    return p


def _make_dataset(
    project_id: str,
    name: str,
    *,
    version="v1",
    record_count=0,
    validation_status="valid",
    is_locked=False,
    created_at=None,
) -> Dataset:
    ds = Dataset(
        project_id=project_id,
        name=name,
        version=version,
        record_count=record_count,
        validation_status=validation_status,
        validation_report=None,
        is_locked=is_locked,
    )
    ds.id = str(uuid.uuid4())
    ds.created_at = created_at or _utc()
    return ds


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.clear()


def _use_projects(projects: list[Project]) -> None:
    svc = ProjectService(FakeProjectRepo(projects))
    app.dependency_overrides[get_project_service] = lambda: svc


def _use_datasets(datasets: list[Dataset]) -> None:
    svc = DatasetService(
        None, max_records=1000,
        project_repo=FakeProjectRepo(), dataset_repo=FakeDatasetRepo(datasets),
    )
    app.dependency_overrides[get_dataset_service] = lambda: svc


def _client() -> TestClient:
    return TestClient(app)


# ---------------- 1-6: GET /api/projects ----------------


def test_1_projects_returns_200_envelope_with_fields() -> None:
    p = _make_project("finance")
    _use_projects([p])
    resp = _client().get("/api/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["total"] == 1 and body["page"] == 1 and body["page_size"] == 20
    item = body["items"][0]
    assert set(item) == {"id", "name", "domain", "status", "created_at"}
    assert item["name"] == "finance"


def test_2_projects_pagination() -> None:
    projects = [_make_project(f"p{i}", created_at=_utc(days_ago=i)) for i in range(3)]
    _use_projects(projects)
    c = _client()
    first = c.get("/api/projects", params={"page": 1, "page_size": 2}).json()
    assert first["total"] == 3 and len(first["items"]) == 2
    second = c.get("/api/projects", params={"page": 2, "page_size": 2}).json()
    assert len(second["items"]) == 1


def test_3_projects_status_filter() -> None:
    active = _make_project("a", status="active")
    archived = _make_project("b", status="archived")
    _use_projects([active, archived])
    body = _client().get("/api/projects", params={"status": "archived"}).json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "archived"


def test_4_projects_default_created_at_desc() -> None:
    oldest = _make_project("old", created_at=_utc(days_ago=10))
    newest = _make_project("new", created_at=_utc(days_ago=1))
    _use_projects([oldest, newest])
    body = _client().get("/api/projects").json()
    assert body["items"][0]["id"] == newest.id


def test_4b_projects_sort_name_asc() -> None:
    _use_projects([_make_project("zeta"), _make_project("alpha")])
    body = _client().get("/api/projects", params={"sort": "name", "order": "asc"}).json()
    assert [i["name"] for i in body["items"]] == ["alpha", "zeta"]


def test_5_projects_empty_list() -> None:
    _use_projects([])
    resp = _client().get("/api/projects")
    assert resp.status_code == 200
    assert resp.json()["items"] == [] and resp.json()["total"] == 0


def test_6_projects_invalid_sort_422() -> None:
    _use_projects([_make_project("p")])
    assert _client().get("/api/projects", params={"sort": "bogus"}).status_code == 422
    assert _client().get("/api/projects", params={"order": "sideways"}).status_code == 422
    assert _client().get("/api/projects", params={"page": 0}).status_code == 422


# ---------------- 7-12: GET /api/datasets ----------------


def test_7_datasets_returns_200_envelope() -> None:
    ds = _make_dataset("p1", "finance-golden", version="v2", record_count=300)
    _use_datasets([ds])
    resp = _client().get("/api/datasets")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    item = body["items"][0]
    assert set(item) == {
        "id", "project_id", "name", "version", "record_count",
        "validation_status", "is_locked", "created_at",
    }
    assert item["project_id"] == "p1"
    assert "validation_report" not in item  # list must stay light


def test_8_datasets_project_id_filter() -> None:
    _use_datasets([_make_dataset("p1", "a"), _make_dataset("p2", "b"), _make_dataset("p1", "c")])
    body = _client().get("/api/datasets", params={"project_id": "p1"}).json()
    assert body["total"] == 2
    assert {i["project_id"] for i in body["items"]} == {"p1"}


def test_9_datasets_pagination() -> None:
    datasets = [_make_dataset("p1", f"d{i}", created_at=_utc(days_ago=i)) for i in range(3)]
    _use_datasets(datasets)
    c = _client()
    first = c.get("/api/datasets", params={"page": 1, "page_size": 2}).json()
    assert first["total"] == 3 and len(first["items"]) == 2
    second = c.get("/api/datasets", params={"page": 2, "page_size": 2}).json()
    assert len(second["items"]) == 1


def test_10_datasets_locked_returned() -> None:
    _use_datasets([_make_dataset("p1", "locked-ds", is_locked=True)])
    item = _client().get("/api/datasets").json()["items"][0]
    assert item["is_locked"] is True


def test_11_datasets_version_returned() -> None:
    _use_datasets([_make_dataset("p1", "ds", version="v3")])
    item = _client().get("/api/datasets").json()["items"][0]
    assert item["version"] == "v3"


def test_12_datasets_validation_status_returned() -> None:
    _use_datasets([_make_dataset("p1", "ds", validation_status="invalid")])
    item = _client().get("/api/datasets").json()["items"][0]
    assert item["validation_status"] == "invalid"


def test_12b_datasets_default_created_at_desc_and_empty() -> None:
    _use_datasets([_make_dataset("p1", "old", created_at=_utc(days_ago=5)),
                   _make_dataset("p1", "new", created_at=_utc(days_ago=1))])
    body = _client().get("/api/datasets").json()
    assert body["items"][0]["name"] == "new"
    _use_datasets([])
    empty = _client().get("/api/datasets").json()
    assert empty["items"] == [] and empty["total"] == 0


# ---------------- 13-16: architecture invariants ----------------


def test_13_projects_router_is_thin() -> None:
    src = (ROOT / "app/api/projects.py").read_text(encoding="utf-8")
    for forbidden in ("from app.models", "from app.repositories", "session.execute"):
        assert forbidden not in src, forbidden


def test_14_datasets_router_is_thin() -> None:
    src = (ROOT / "app/api/datasets.py").read_text(encoding="utf-8")
    for forbidden in ("from app.models", "from app.repositories", "session.execute"):
        assert forbidden not in src, forbidden


def test_15_projects_not_derived_from_runs() -> None:
    src = (ROOT / "app/repositories/project.py").read_text(encoding="utf-8")
    assert "EvaluationRun" not in src
    assert "evaluation_runs" not in src
    assert "EvaluationRun" not in (ROOT / "app/services/project_service.py").read_text(encoding="utf-8")


def test_16_datasets_not_derived_from_runs() -> None:
    repo_src = (ROOT / "app/repositories/dataset.py").read_text(encoding="utf-8")
    svc_src = (ROOT / "app/services/dataset_service.py").read_text(encoding="utf-8")
    for src in (repo_src, svc_src):
        assert "EvaluationRun" not in src
        assert "evaluation_runs" not in src
