"""Dataset API integration tests (T-07).

Runs against fake repositories through the service test seam — no database,
no network (TEST_DATABASE_URL isolation is preserved). API -> Service ->
Repository chain is exercised end-to-end via dependency_overrides.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_dataset_service
from app.main import app
from app.models import Dataset
from app.services.dataset_service import DatasetService


class FakeProjectRepo:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists

    def get(self, project_id: str):
        if not self.exists:
            return None
        return object()  # any non-None sentinel


class FakeDatasetRepo:
    def __init__(self) -> None:
        self.datasets: list[Dataset] = []
        self.inserted_records: dict[str, int] = {}

    def get(self, dataset_id: str):
        return next((d for d in self.datasets if d.id == dataset_id), None)

    def get_max_version(self, project_id: str, name: str) -> int | None:
        nums = [
            int(d.version[1:])
            for d in self.datasets
            if d.project_id == project_id and d.name == name
        ]
        return max(nums) if nums else None

    def create_version_with_records(
        self, project_id, name, *, record_count, validation_status, validation_report, records
    ):
        last = self.get_max_version(project_id, name) or 0
        ds = Dataset(
            project_id=project_id,
            name=name,
            version=f"v{last + 1}",
            record_count=record_count,
            validation_status=validation_status,
            validation_report=validation_report,
            is_locked=False,
        )
        ds.id = str(uuid.uuid4())
        ds.created_at = datetime.now(UTC)
        self.datasets.append(ds)
        if records:
            self.inserted_records[ds.id] = len(records)
        return ds

    def list_records_page(self, dataset_id: str, page: int, page_size: int):
        from types import SimpleNamespace

        rows = [
            SimpleNamespace(
                row_index=i, question=f"q{i}", reference_answer=None,
                reference_contexts=None, metadata_={},
            )
            for i in range(self.inserted_records.get(dataset_id, 0))
        ]
        start = (page - 1) * page_size
        return rows[start : start + page_size], len(rows)


def _make_service(records_exist: bool = True) -> tuple[DatasetService, FakeDatasetRepo]:
    fd = FakeDatasetRepo()
    svc = DatasetService(None, max_records=1000, project_repo=FakeProjectRepo(records_exist), dataset_repo=fd)
    return svc, fd


def _use(svc: DatasetService) -> None:
    app.dependency_overrides[get_dataset_service] = lambda: svc


def _payload(**overrides) -> bytes:
    body = {
        "name": "finance-golden",
        "domain": "financial",
        "duplicate_policy": "strict",
        "records": [
            {"question": f"q{i}", "reference_answer": "a", "reference_contexts": ["c"],
             "metadata": {"domain": "financial"}}
            for i in range(3)
        ],
    }
    body.update(overrides)
    import json

    return json.dumps(body).encode("utf-8")


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.pop(get_dataset_service, None)


def test_import_success_201_valid_v1() -> None:
    svc, fd = _make_service()
    _use(svc)
    c = TestClient(app)
    resp = c.post("/api/projects/p1/datasets:import", content=_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"] == "v1"
    assert body["validation_status"] == "valid"
    assert body["record_count"] == 3
    assert body["is_locked"] is False
    assert fd.inserted_records[body["id"]] == 3


def test_import_parse_error_400_nothing_persisted() -> None:
    svc, fd = _make_service()
    _use(svc)
    c = TestClient(app)
    resp = c.post("/api/projects/p1/datasets:import", content=b"{not json")
    assert resp.status_code == 400
    assert resp.json()["code"] == "BIZ_ADAPTER_PARSE_ERROR"
    assert fd.datasets == []


def test_import_over_limit_400() -> None:
    svc, fd = _make_service()
    _use(svc)
    c = TestClient(app)
    big = _payload(records=[{"question": f"q{i}"} for i in range(1001)])
    resp = c.post("/api/projects/p1/datasets:import", content=big)
    assert resp.status_code == 400
    assert resp.json()["code"] == "BIZ_ADAPTER_PARSE_ERROR"
    assert fd.datasets == []


def test_import_validation_failed_400_invalid_persisted_records_not_inserted() -> None:
    svc, fd = _make_service()
    _use(svc)
    c = TestClient(app)
    bad = _payload(records=[{"question": "ok"}, {"reference_answer": "no question"}, {"question": "q2"}])
    resp = c.post("/api/projects/p1/datasets:import", content=bad)
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "BIZ_VALIDATION_FAILED"
    report = body["context"]["validation_report"]
    assert report["valid"] is False
    schema_check = next(c for c in report["checks"] if c["name"] == "schema")
    assert schema_check["issues"][0]["row_index"] == 1  # locatable
    # dataset persisted as invalid, zero dirty records entered
    assert len(fd.datasets) == 1
    assert fd.datasets[0].validation_status == "invalid"
    assert fd.datasets[0].record_count == 3
    assert fd.inserted_records.get(fd.datasets[0].id, 0) == 0


def test_import_duplicate_skip_policy_dedupes() -> None:
    svc, fd = _make_service()
    _use(svc)
    c = TestClient(app)
    payload = _payload(duplicate_policy="skip", records=[
        {"question": "same", "reference_answer": "a"},
        {"question": "other", "reference_answer": "a"},
        {"question": " SAME ", "reference_answer": "a"},
    ])
    resp = c.post("/api/projects/p1/datasets:import", content=payload)
    assert resp.status_code == 201
    ds_id = resp.json()["id"]
    assert fd.inserted_records[ds_id] == 2  # later duplicate dropped
    # …and the recorded COUNT must describe what was stored, not what was parsed.
    # This assertion was the gap: the rows were deduplicated correctly while
    # record_count still said 3, so the dataset list showed "3 条记录" above a
    # 2-row table, and a run created on it started with total_records=3 until the
    # runner corrected it.
    recorded = next(d for d in fd.datasets if d.id == ds_id)
    assert recorded.record_count == 2


def test_import_project_not_found_404() -> None:
    svc, fd = _make_service()
    svc._projects = FakeProjectRepo(exists=False)
    _use(svc)
    c = TestClient(app)
    resp = c.post("/api/projects/missing/datasets:import", content=_payload())
    assert resp.status_code == 404
    assert resp.json()["code"] == "BIZ_NOT_FOUND"


def test_get_dataset_200_and_404() -> None:
    svc, fd = _make_service()
    _use(svc)
    c = TestClient(app)
    created = c.post("/api/projects/p1/datasets:import", content=_payload()).json()
    ok = c.get(f"/api/datasets/{created['id']}")
    assert ok.status_code == 200
    assert ok.json()["name"] == "finance-golden"
    assert c.get("/api/datasets/missing-id").status_code == 404


def test_records_pagination_envelope() -> None:
    svc, fd = _make_service()
    _use(svc)
    c = TestClient(app)
    created = c.post("/api/projects/p1/datasets:import", content=_payload()).json()
    resp = c.get(f"/api/datasets/{created['id']}/records", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["total"] == 3 and body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["row_index"] == 0


def test_validation_endpoint_returns_report() -> None:
    svc, fd = _make_service()
    _use(svc)
    c = TestClient(app)
    created = c.post("/api/projects/p1/datasets:import", content=_payload()).json()
    resp = c.get(f"/api/datasets/{created['id']}/validation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["validation_status"] == "valid"
    assert [c["name"] for c in body["validation_report"]["checks"]] == [
        "schema", "duplicate", "missing_field", "reference", "domain_metadata",
    ]
