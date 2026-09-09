"""Versioning tests (FR-03 / ADR-06 / PRD Q7): re-import same name -> next
version; referenced (locked) versions are immutable and untouched."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime

from app.models import Dataset
from app.services.dataset_service import DatasetService
from tests.test_dataset_api import FakeDatasetRepo, FakeProjectRepo, _payload


def _repo_with_locked_v1() -> FakeDatasetRepo:
    fd = FakeDatasetRepo()
    v1 = Dataset(
        project_id="p1",
        name="finance-golden",
        version="v1",
        record_count=3,
        validation_status="valid",
        validation_report={"valid": True, "checks": []},
        is_locked=True,  # referenced by a Run -> immutable (ADR-06)
    )
    v1.id = str(uuid.uuid4())
    v1.created_at = datetime.now(UTC)
    fd.datasets.append(v1)
    return fd


def _service(fd: FakeDatasetRepo) -> DatasetService:
    return DatasetService(None, max_records=1000, project_repo=FakeProjectRepo(), dataset_repo=fd)


def test_reimport_same_name_creates_v2_and_leaves_locked_v1_untouched() -> None:
    fd = _repo_with_locked_v1()
    v1 = fd.datasets[0]
    svc = _service(fd)

    ds = svc.import_dataset("p1", _payload())

    assert ds.version == "v2"
    assert ds.id != v1.id
    # v1 immutable: same id, same locked flag, same report, no writes
    assert fd.datasets[0].id == v1.id
    assert fd.datasets[0].is_locked is True
    assert fd.datasets[0].validation_report == v1.validation_report
    assert fd.inserted_records.get(v1.id) is None  # no records added to v1
    assert fd.inserted_records[ds.id] == 3


def test_version_increments_across_invalid_and_valid() -> None:
    fd = FakeDatasetRepo()
    svc = _service(fd)
    # invalid import (missing question) consumes v1 (audit trail), valid import -> v2
    bad = _payload(records=[{"reference_answer": "x"}])
    with contextlib.suppress(Exception):
        svc.import_dataset("p1", bad)
    assert fd.datasets[0].version == "v1"
    assert fd.datasets[0].validation_status == "invalid"

    ds = svc.import_dataset("p1", _payload())
    assert ds.version == "v2"
    assert ds.validation_status == "valid"


def test_same_name_different_projects_independent_versions() -> None:
    fd = FakeDatasetRepo()
    svc = _service(fd)
    a = svc.import_dataset("p1", _payload())
    b = svc.import_dataset("p2", _payload())
    assert (a.version, b.version) == ("v1", "v1")
