from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.models import Dataset, EvaluationConfig
from app.repositories.evaluation import EvaluationRepository
from tests.test_evaluation_api import _post
from tests.test_evaluation_api import ctx as ctx


@pytest.mark.parametrize("validation_status,record_count", [("invalid", 3), ("valid", 0)])
def test_api_rejects_unusable_dataset_without_run_or_lock(ctx, monkeypatch, validation_status, record_count):
    dataset = SimpleNamespace(
        id="ds1", name="invalid import", version="v1", project_id="p1",
        validation_status=validation_status, record_count=record_count, is_locked=False,
    )
    monkeypatch.setattr(ctx.repo, "get_dataset", lambda _: dataset)
    response = _post(ctx)
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "BIZ_DATASET_NOT_EVALUABLE"
    assert body["context"]["dataset_id"] == "ds1"
    assert body["context"]["validation_status"] == validation_status
    assert ctx.repo.runs == {}
    assert ctx.repo.datasets["ds1"]["is_locked"] is False
    assert ctx.launcher.calls == []


@pytest.mark.parametrize(
    "status,count,dataset_project,config_project,code",
    [
        ("invalid", 3, "p1", "p1", "BIZ_DATASET_NOT_EVALUABLE"),
        ("valid", 0, "p1", "p1", "BIZ_DATASET_NOT_EVALUABLE"),
        ("valid", 3, "p2", "p1", "BIZ_PROJECT_MISMATCH"),
        ("valid", 3, "p1", "p2", "BIZ_PROJECT_MISMATCH"),
    ],
)
def test_repository_rejects_invalid_run_before_any_write(status, count, dataset_project, config_project, code):
    dataset = Dataset(
        id="ds1", project_id=dataset_project, name="sample", version="v1",
        validation_status=status, record_count=count, is_locked=False,
    )
    config = EvaluationConfig(id="c1", project_id=config_project, name="default", config_version="v1")
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = dataset
    session.get.return_value = config

    with pytest.raises(AppError) as raised:
        EvaluationRepository(session).create_run_with_lock(
            project_id="p1", dataset_id="ds1", config_id="c1", reproducibility_meta={},
        )

    assert raised.value.code == code
    assert dataset.is_locked is False
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
