"""T-18 config catalog API tests — GET /api/configs/profiles, GET /api/metrics,
POST /api/projects/{pid}/configs.

The read catalog (profiles/metrics) is exercised against the REAL default
ConfigService + MetricRegistry (reads the actual config/ YAML trees, no DB — the
catalog is intentionally database-independent). The config write path is
exercised through dependency_overrides with fake repos (no DB, no network).

Architecture invariants (thin router, no second config/metric source) are checked
via source reading, matching the existing test_project_dataset_read_api pattern.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_config_resource_service
from app.core.errors import ConfigInvalidError, NotFoundError
from app.main import app
from app.services.config_resource_service import ConfigResourceService

ROOT = Path(__file__).resolve().parents[1]

ALL_METRIC_FIELDS = {
    "name", "category", "engine", "version", "description",
    "input_requirements", "direction", "default_severity",
}


# ---------------- fake repositories (config write path only) ----------------


class FakeProjectRepo:
    def __init__(self, project_id: str | None = None, *, exists: bool = True) -> None:
        self._pid = project_id or str(uuid.uuid4())
        self._exists = exists

    def get(self, project_id: str):
        if self._exists and project_id == self._pid:
            return SimpleNamespace(id=self._pid)
        return None


class FakeEvalRepo:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], SimpleNamespace] = {}

    def get_config_by_name(self, project_id: str, name: str):
        return self._rows.get((project_id, name))

    def create_config(self, *, project_id, name, domain_config, profile_config,
                      pipeline_config, config_version) -> SimpleNamespace:
        row = SimpleNamespace(
            id=str(uuid.uuid4()),
            name=name,
            domain_config=domain_config,
            profile_config=profile_config,
            pipeline_config=pipeline_config,
            config_version=config_version,
        )
        self._rows[(project_id, name)] = row
        return row


# ---------------- fixtures ----------------


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app)


def _use_config_resource(project_id: str, eval_repo: FakeEvalRepo, project_repo: FakeProjectRepo):
    svc = ConfigResourceService(eval_repo, project_repo)
    app.dependency_overrides[get_config_resource_service] = lambda: svc


# ---------------- 1-4: GET /api/configs/profiles ----------------


def test_1_profiles_returns_envelope_and_default() -> None:
    resp = _client().get("/api/configs/profiles")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total"}
    assert body["total"] >= 1
    names = {i["name"] for i in body["items"]}
    assert "default" in names


def test_2_profile_item_has_full_safe_fields() -> None:
    body = _client().get("/api/configs/profiles").json()
    item = next(i for i in body["items"] if i["name"] == "default")
    # every field the wizard needs is present — and safe (no secrets)
    # Phase D: + judge（公共字段）+ pipeline（Review 页展示输入）
    assert set(item) == {
        "name", "version", "domain", "metrics",
        "severity_mapping", "quality_gate", "rag_input", "judge", "pipeline",
    }
    assert item["version"] == "v1"
    assert item["domain"] == "general"
    assert isinstance(item["metrics"], list) and len(item["metrics"]) >= 1
    m = item["metrics"][0]
    assert set(m) == {"name", "enabled", "threshold", "weight"}
    # rag_input is mode-aware (Phase D §6)
    assert item["rag_input"]["mode"] in ("golden_replay", "http")
    # judge carries ONLY the public fields — never a secret
    assert set(item["judge"]) == {
        "provider", "model", "model_version", "temperature",
        "max_tokens", "timeout", "retry",
    }
    # severity_mapping is a real dict (never empty from the default profile)
    assert isinstance(item["severity_mapping"], dict)
    # sqlite / password secrets must NEVER leak into the profile payload
    assert "database" not in str(item)
    assert "password" not in str(item).lower()
    assert "api_key" not in str(item).lower()


def test_3_profiles_metrics_include_numerical_consistency() -> None:
    body = _client().get("/api/configs/profiles").json()
    names = {m["name"] for i in body["items"] for m in i["metrics"]}
    assert "numerical_consistency" in names


def test_3b_e2e_profile_carries_quality_gate_and_thresholds() -> None:
    body = _client().get("/api/configs/profiles").json()
    item = next(i for i in body["items"] if i["name"] == "e2e")
    assert item["quality_gate"] is not None
    assert item["quality_gate"]["enabled"] is True
    nc = next(m for m in item["metrics"] if m["name"] == "numerical_consistency")
    assert nc["threshold"] == 0.9


def test_4_get_profile_by_name_and_404() -> None:
    ok = _client().get("/api/configs/profiles/default")
    assert ok.status_code == 200
    assert ok.json()["name"] == "default"
    missing = _client().get("/api/configs/profiles/nope")
    assert missing.status_code == 404
    assert missing.json()["code"] == "BIZ_NOT_FOUND"


# ---------------- 5-6: GET /api/metrics ----------------


def test_5_metrics_returns_list_of_registered_metrics() -> None:
    resp = _client().get("/api/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total"}
    assert body["total"] >= 3
    for item in body["items"]:
        assert set(item) == ALL_METRIC_FIELDS
        assert item["direction"] in {"higher_is_better", "lower_is_better"}
        assert isinstance(item["input_requirements"], list)
        assert item["engine"] and isinstance(item["engine"], str)


def test_6_metric_catalog_metadata() -> None:
    body = _client().get("/api/metrics").json()
    by_name = {i["name"]: i for i in body["items"]}
    assert "numerical_consistency" in by_name
    nc = by_name["numerical_consistency"]
    assert nc["category"] == "integrity"
    assert nc["direction"] == "higher_is_better"
    # ragas metrics are present too
    assert "faithfulness" in by_name
    assert by_name["faithfulness"]["category"] == "generation"


# ---------------- 7-10: POST /api/projects/{pid}/configs ----------------


def _post_config(client: TestClient, project_id: str, *, profile="default", name="cf"):
    return client.post(
        f"/api/projects/{project_id}/configs",
        json={"name": name, "domain": "general", "profile": profile},
    )


def test_7_create_config_201_with_config_id_and_version() -> None:
    pid = str(uuid.uuid4())
    _use_config_resource(pid, FakeEvalRepo(), FakeProjectRepo(pid))
    resp = _post_config(_client(), pid)
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"config_id", "config_version", "name", "domain", "profile", "created"}
    assert body["config_id"]
    assert body["config_version"]
    assert body["created"] is True
    assert body["profile"] == "default"


def test_8_config_reuse_is_idempotent() -> None:
    pid = str(uuid.uuid4())
    repo = FakeEvalRepo()
    _use_config_resource(pid, repo, FakeProjectRepo(pid))
    c = _client()
    first = _post_config(c, pid, name="shared").json()
    second = _post_config(c, pid, name="shared").json()
    assert first["created"] is True
    assert second["created"] is False
    assert second["config_id"] == first["config_id"]


def test_9_unknown_project_404() -> None:
    pid = str(uuid.uuid4())
    _use_config_resource(pid, FakeEvalRepo(), FakeProjectRepo(pid, exists=False))
    resp = _post_config(_client(), pid)
    assert resp.status_code == 404
    assert resp.json()["code"] == "BIZ_NOT_FOUND"


def test_10_unknown_profile_409() -> None:
    pid = str(uuid.uuid4())
    _use_config_resource(pid, FakeEvalRepo(), FakeProjectRepo(pid))
    resp = _post_config(_client(), pid, profile="nope")
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_CONFIG_INVALID"


# ---------------- unit: ConfigResourceService.save ----------------
# (direct calls — no HTTP — to pin the error/created semantics)


def test_11_save_unit_unknown_project_raises() -> None:
    svc = ConfigResourceService(FakeEvalRepo(), FakeProjectRepo(exists=False))
    with pytest.raises(NotFoundError):
        svc.save(project_id="x", name="n", profile="default")


def test_12_save_unit_unknown_profile_raises() -> None:
    svc = ConfigResourceService(FakeEvalRepo(), FakeProjectRepo("x"))
    with pytest.raises(ConfigInvalidError):
        svc.save(project_id="x", name="n", profile="nope")


def test_13_save_unit_created_then_reused() -> None:
    eval_repo = FakeEvalRepo()
    svc = ConfigResourceService(eval_repo, FakeProjectRepo("x"))
    row1, created1, version1 = svc.save(project_id="x", name="n", profile="default")
    row2, created2, version2 = svc.save(project_id="x", name="n", profile="default")
    assert created1 is True and created2 is False
    assert row1.id == row2.id
    assert version1 == version2


def test_14_config_version_is_stable_sha256() -> None:
    eval_repo = FakeEvalRepo()
    svc = ConfigResourceService(eval_repo, FakeProjectRepo("x"))
    _, _, v1 = svc.save(project_id="x", name="a", profile="e2e")
    _, _, v2 = svc.save(project_id="x", name="b", profile="e2e")
    assert v1 == v2
    assert len(v1) == 64


# ---------------- 15-16: architecture invariants (source read) ----------------


def test_15_configs_router_is_thin() -> None:
    src = (ROOT / "app/api/configs.py").read_text(encoding="utf-8")
    for forbidden in ("from app.models", "from app.repositories", "session.execute"):
        assert forbidden not in src, forbidden


def test_16_catalog_has_no_second_config_or_orm_source() -> None:
    src = (ROOT / "app/services/catalog_service.py").read_text(encoding="utf-8")
    # catalog is the read mirror — it must never create/own a Session or touch ORM
    assert "EvaluationRun" not in src
    assert "Session(" not in src
    assert "session.execute" not in src
    # rag_input is normalized by the SAME helper the run planner uses (mode-aware,
    # Phase D) — no second rag-input source, database.url never extracted
    assert "normalize_rag_input" in src
    assert 'system.get("rag_input")' not in src
    assert "get(\"database\")" not in src
    assert "get(\"api_key\")" not in src
    # judge serialization must list PUBLIC keys only — api_key/password never extracted
    judge_block = src[src.index("judge_src.get(k)"):src.index("\"pipeline\"")]
    assert "api_key" not in judge_block
    assert "password" not in judge_block
