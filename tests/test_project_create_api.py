"""Phase 1A G1 — POST /api/projects contract tests.

Fake repository + dependency_overrides (existing convention, no DB). The real
PostgreSQL path is covered by tests/test_phase1a_integration.py once
TEST_DATABASE_URL is provisioned.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_project_service
from app.main import app
from app.models import Project
from app.services.project_service import ProjectService

AVAILABLE_DOMAINS = ["finance", "general"]


class FakeProjectCreateRepo:
    """In-memory mirror of ProjectRepository (get_by_name/create/list_page)."""

    def __init__(self) -> None:
        self.projects: list[Project] = []

    def get(self, project_id: str):
        return next((p for p in self.projects if p.id == project_id), None)

    def get_by_name(self, name: str):
        return next((p for p in self.projects if p.name == name), None)

    def create(self, *, name: str, domain: str, status: str = "active") -> Project:
        p = Project(name=name, domain=domain, status=status)
        p.id = f"proj-{len(self.projects) + 1}"
        p.created_at = datetime.now(UTC)
        self.projects.append(p)
        return p

    def list_page(self, *, page, page_size, sort="created_at", order="desc", status=None):
        rows = [p for p in self.projects if status is None or p.status == status]
        rows = sorted(rows, key=lambda p: p.created_at, reverse=(order == "desc"))
        start = (page - 1) * page_size
        return rows[start : start + page_size], len(rows)


@pytest.fixture(autouse=True)
def _env():
    repo = FakeProjectCreateRepo()
    svc = ProjectService(repo, valid_domains=AVAILABLE_DOMAINS)
    app.dependency_overrides[get_project_service] = lambda: svc
    yield repo
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app)


def test_1_create_returns_201_with_fields() -> None:
    resp = _client().post("/api/projects", json={"name": "finance-eval"})
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "name", "domain", "status", "created_at"}
    assert body["name"] == "finance-eval"
    assert body["domain"] == "general"  # default
    assert body["status"] == "active"  # default


def test_2_created_row_is_listable_and_persisted_in_repo() -> None:
    c = _client()
    created = c.post("/api/projects", json={"name": "p1"}).json()
    listing = c.get("/api/projects").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == created["id"]


def test_3_empty_name_400() -> None:
    for bad in ("", "   "):
        resp = _client().post("/api/projects", json={"name": bad})
        assert resp.status_code == 400, bad
        assert resp.json()["code"] == "BIZ_VALIDATION_FAILED"


def test_4_name_is_stripped() -> None:
    body = _client().post("/api/projects", json={"name": "  spaced  "}).json()
    assert body["name"] == "spaced"


def test_5_overlong_name_400() -> None:
    resp = _client().post("/api/projects", json={"name": "x" * 201})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BIZ_VALIDATION_FAILED"


def test_6_unknown_domain_400() -> None:
    resp = _client().post("/api/projects", json={"name": "p", "domain": "legal"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BIZ_VALIDATION_FAILED"
    assert "available" in resp.json()["detail"]


def test_7_known_domain_accepted() -> None:
    body = _client().post("/api/projects", json={"name": "p", "domain": "finance"}).json()
    assert body["domain"] == "finance"


def test_8_duplicate_name_409() -> None:
    c = _client()
    assert c.post("/api/projects", json={"name": "dup"}).status_code == 201
    resp = c.post("/api/projects", json={"name": "dup"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_PROJECT_NAME_EXISTS"
    assert resp.json()["detail"]  # explicit, actionable message


def test_9_archived_name_stays_reserved() -> None:
    c = _client()
    c.post("/api/projects", json={"name": "gone", "status": "archived"})
    resp = c.post("/api/projects", json={"name": "gone"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_PROJECT_NAME_EXISTS"


def test_10_invalid_status_422() -> None:
    resp = _client().post("/api/projects", json={"name": "p", "status": "deleted"})
    assert resp.status_code == 422


def test_11_explicit_archived_accepted() -> None:
    body = _client().post("/api/projects", json={"name": "p", "status": "archived"}).json()
    assert body["status"] == "archived"


def test_12_router_stays_thin() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/api/projects.py").read_text(encoding="utf-8")
    for forbidden in ("from app.models", "from app.repositories", "session.execute"):
        assert forbidden not in src, forbidden


def test_13_service_validates_domain_against_config_source() -> None:
    # The dependency wiring must feed domains from ConfigService (layer-2 YAML),
    # never a hardcoded list inside the service.
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/api/deps.py").read_text(encoding="utf-8")
    assert "domain_names()" in src
