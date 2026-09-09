"""Lock tests (I-1 preparation, service level). Real row-lock race (I-1b,
two concurrent runs) needs TEST_DATABASE_URL — deferred to T-22 per plan.

T-07 guarantee: is_locked=True on an existing version blocks nothing for NEW
versions (the sanctioned correction path, Spec §6.5 "引导创建新版本") but no
service API can mutate a locked version's records — mutation entry points
simply do not exist on DatasetService, and DatasetLockedError (409) is the
contract reserved for run-creation (T-12).
"""

from __future__ import annotations

from app.core.errors import DatasetLockedError
from tests.test_dataset_api import FakeDatasetRepo, _payload
from tests.test_dataset_versioning import _repo_with_locked_v1, _service


def test_dataset_locked_error_contract() -> None:
    err = DatasetLockedError("dataset locked")
    assert err.code == "BIZ_DATASET_LOCKED"
    assert err.http_status == 409
    body = err.body()
    assert body["code"] == "BIZ_DATASET_LOCKED"
    assert set(body) == {"detail", "code", "trace_id", "context"}


def test_import_over_locked_version_creates_new_version_not_mutate() -> None:
    fd = _repo_with_locked_v1()
    v1 = fd.datasets[0]
    svc = _service(fd)

    ds = svc.import_dataset("p1", _payload())

    assert ds.version == "v2"
    assert v1.is_locked is True
    assert v1.id == fd.datasets[0].id  # same row, never rewritten


def test_service_has_no_record_mutation_entry_points() -> None:
    svc = _service(FakeDatasetRepo())
    public = [m for m in dir(svc) if not m.startswith("_")]
    mutating = [m for m in public if any(k in m for k in ("update", "delete", "replace", "edit"))]
    assert mutating == []
