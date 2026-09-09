"""Configuration Lifecycle v1 — Phase B tests (import / validation / version).

Covers task §25 items 1-12 plus the approved Freeze-level invariants:
  1  valid YAML import                    9  version creation (v1 -> v2)
  2  invalid YAML syntax                  10 old version immutable
  3  invalid metric                       11 config_version reproducibility
  4  invalid threshold                    12 export/import roundtrip
  5  invalid weight                       +  secret isolation (invariant 7)
  6  invalid severity                     +  run uses stored body end-to-end
  7  invalid quality gate                 +  quality gate reads stored body
  8  invalid pipeline                     +  legacy pointer rows unaffected

Import service exercises the REAL parse/validate/diff/version logic and the
REAL ConfigService hashing; only the Repository is faked (in-memory, mirroring
EvaluationRepository's surface, same pattern as test_config_catalog_api).
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.deps import (
    get_config_import_service,
    get_config_service,
    get_evaluation_launcher,
    get_evaluation_service,
)
from app.core.errors import NotFoundError
from app.engines.integrity import IntegrityEngine
from app.main import app
from app.runner.local import LocalAsyncRunner
from app.services.config_import_service import ConfigImportService
from app.services.config_service import ConfigService, default_config_service
from app.services.evaluation_service import EvaluationService
from tests.test_evaluation_service import FakeEvaluationRepo, _record_row

PROJECT = "proj-1"


VALID_YAML = """\
profile:
  name: strict_financial
metrics:
  faithfulness: { enabled: true, threshold: 0.9, weight: 2.0 }
  numerical_consistency: { enabled: true, threshold: null, weight: 1.0 }
severity_mapping:
  numerical_mismatch: CRITICAL
pipeline:
  engines: [ragas, integrity]
  diagnosis: { enabled: true }
quality_gate: null
"""


# ---------------- fakes (import write path only) ----------------


class FakeProjectRepo:
    def __init__(self, known=(PROJECT, "proj-2")) -> None:
        self.known = set(known)

    def get(self, project_id: str):
        return SimpleNamespace(id=project_id) if project_id in self.known else None


class FakeConfigRepo:
    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    def create_config(self, *, project_id, name, domain_config, profile_config,
                      pipeline_config, config_version):
        row = SimpleNamespace(
            id=str(uuid.uuid4()), project_id=project_id, name=name,
            domain_config=domain_config, profile_config=profile_config,
            pipeline_config=pipeline_config, config_version=config_version,
            created_at=datetime.now(UTC),
        )
        self.rows[row.id] = row
        return row

    def get_config(self, config_id: str):
        if config_id not in self.rows:
            raise NotFoundError(f"evaluation config {config_id} not found")
        return self.rows[config_id]

    def get_config_by_name(self, project_id: str, name: str):
        for row in self.rows.values():
            if row.project_id == project_id and row.name == name:
                return row
        return None

    def list_configs_for_project(self, project_id: str) -> list:
        return [r for r in self.rows.values() if r.project_id == project_id]


@pytest.fixture()
def import_ctx():
    repo = FakeConfigRepo()
    svc = ConfigImportService(repo, FakeProjectRepo(), default_config_service)
    app.dependency_overrides[get_config_import_service] = lambda: svc
    client = TestClient(app)
    yield SimpleNamespace(client=client, repo=repo, svc=svc)
    app.dependency_overrides.clear()


def _import(client, yaml_text=VALID_YAML, project_id=PROJECT, expect=201):
    resp = client.post("/api/configs/import",
                       json={"project_id": project_id, "domain": "general", "yaml": yaml_text})
    assert resp.status_code == expect, resp.text
    return resp


def _errors(resp) -> list[dict]:
    return resp.json()["context"]["errors"]


# ---------------- 1: valid import ----------------


def test_valid_import_creates_v1(import_ctx):
    data = _import(import_ctx.client).json()
    assert data["created"] is True
    assert data["profile"] == "strict_financial"
    assert data["version"] == "v1"
    assert data["config_version"] and len(data["config_version"]) == 64
    assert data["metrics"][0]["name"] == "faithfulness"
    assert data["metrics"][0]["threshold"] == 0.9
    assert data["diff"]["base"] is None  # no stored version, no same-named deployment profile
    row = next(iter(import_ctx.repo.rows.values()))
    assert row.name == "strict_financial:v1"
    # invariant 6: stored body == imported body
    assert row.profile_config["body"] == yaml.safe_load(VALID_YAML)
    assert row.profile_config["source"] == "imported"


# ---------------- preview (no persistence) ----------------


def test_preview_does_not_persist(import_ctx):
    resp = import_ctx.client.post("/api/configs/import/preview",
                                  json={"project_id": PROJECT, "domain": "general", "yaml": VALID_YAML})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["version"] == "v1"
    assert data["judge"]["temperature"] is not None  # public judge fields surfaced
    assert import_ctx.repo.rows == {}  # nothing persisted


def test_preview_diff_against_stored_base(import_ctx):
    _import(import_ctx.client)
    modified = VALID_YAML.replace("threshold: 0.9", "threshold: 0.95")
    resp = import_ctx.client.post("/api/configs/import/preview",
                                  json={"project_id": PROJECT, "domain": "general", "yaml": modified})
    items = resp.json()["diff"]["items"]
    changed = [(i["path"], i["old"], i["new"]) for i in items if i["change"] == "changed"]
    assert ("metrics.faithfulness.threshold", 0.9, 0.95) in changed


# ---------------- 2-8: validation failures -> 422 with structured paths ----------------


def test_invalid_yaml_syntax(import_ctx):
    resp = _import(import_ctx.client, "profile: [unclosed", expect=422)
    assert resp.json()["code"] == "BIZ_CONFIG_IMPORT_INVALID"
    assert _errors(resp)[0]["path"] == "yaml"


def test_unknown_metric(import_ctx):
    bad = VALID_YAML.replace("  faithfulness:", "  no_such_metric:")
    resp = _import(import_ctx.client, bad, expect=422)
    assert _errors(resp)[0]["path"] == "metrics.no_such_metric"


def test_invalid_threshold(import_ctx):
    bad = VALID_YAML.replace("threshold: 0.9", "threshold: 1.5")
    resp = _import(import_ctx.client, bad, expect=422)
    assert _errors(resp)[0]["path"] == "metrics.faithfulness.threshold"


def test_invalid_weight(import_ctx):
    bad = VALID_YAML.replace("weight: 2.0", "weight: -1")
    resp = _import(import_ctx.client, bad, expect=422)
    assert _errors(resp)[0]["path"] == "metrics.faithfulness.weight"


def test_invalid_severity(import_ctx):
    bad = VALID_YAML.replace("numerical_mismatch: CRITICAL", "numerical_mismatch: SUPER_BAD")
    resp = _import(import_ctx.client, bad, expect=422)
    assert _errors(resp)[0]["path"] == "severity_mapping.numerical_mismatch"


def test_invalid_quality_gate(import_ctx):
    bad = VALID_YAML.replace(
        "quality_gate: null",
        "quality_gate:\n  enabled: true\n  required_metrics: [no_such_metric]",
    )
    resp = _import(import_ctx.client, bad, expect=422)
    assert _errors(resp)[0]["path"] == "quality_gate.required_metrics.no_such_metric"


def test_invalid_pipeline_engine(import_ctx):
    bad = VALID_YAML.replace("engines: [ragas, integrity]", "engines: [warp_drive]")
    resp = _import(import_ctx.client, bad, expect=422)
    assert _errors(resp)[0]["path"] == "pipeline.engines[0]"


def test_invalid_rag_input_mode_mix(import_ctx):
    bad = VALID_YAML.replace(
        "quality_gate: null",
        "rag_input:\n  mode: golden_replay\n  url: http://x",
    )
    resp = _import(import_ctx.client, bad, expect=422)
    assert _errors(resp)[0]["path"] == "rag_input.url"


# ---------------- invariant 7: secret isolation at import ----------------


@pytest.mark.parametrize("replacement,expected", [
    ("profile:\n  name: strict_financial\n  password: hunter2\n", "profile.password"),
    ("  faithfulness: { enabled: true, threshold: 0.9, weight: 2.0, api_key: sk-1 }",
     "metrics.faithfulness.api_key"),
])
def test_secret_keys_rejected(import_ctx, replacement, expected):
    bad = VALID_YAML.replace("profile:\n  name: strict_financial\n", replacement) \
        if replacement.startswith("profile") else \
        VALID_YAML.replace("  faithfulness: { enabled: true, threshold: 0.9, weight: 2.0 }", replacement)
    resp = _import(import_ctx.client, bad, expect=422)
    assert expected in [e["path"] for e in _errors(resp)]


def test_env_tokens_rejected_in_body(import_ctx):
    bad = VALID_YAML.replace("quality_gate: null", 'rag_input:\n  mode: http\n  url: "${RAG_URL}"')
    resp = _import(import_ctx.client, bad, expect=422)
    assert any("environment tokens" in e["message"] for e in _errors(resp))


# ---------------- 9/10: version creation + immutability ----------------


def test_version_creation_v2(import_ctx):
    _import(import_ctx.client)
    modified = VALID_YAML.replace("threshold: 0.9", "threshold: 0.95")
    data = _import(import_ctx.client, modified).json()
    assert data["version"] == "v2"
    assert data["created"] is True
    assert len(import_ctx.repo.rows) == 2
    assert data["diff"]["base"] == "v1"


def test_old_version_immutable(import_ctx):
    first = _import(import_ctx.client).json()
    v1_row = next(iter(import_ctx.repo.rows.values()))
    body_before = dict(v1_row.profile_config)
    version_before = v1_row.config_version

    modified = VALID_YAML.replace("threshold: 0.9", "threshold: 0.95")
    _import(import_ctx.client, modified)

    # invariant 2: v1 row NOT updated/deleted; history keeps pointing at it
    assert dict(v1_row.profile_config) == body_before
    assert v1_row.config_version == version_before
    assert import_ctx.repo.get_config(v1_row.id).profile_config["version"] == "v1"
    assert first["config_id"] == v1_row.id


def test_identical_reimport_is_idempotent(import_ctx):
    first = _import(import_ctx.client).json()
    again = _import(import_ctx.client).json()
    assert again["created"] is False
    assert again["config_id"] == first["config_id"]
    assert len(import_ctx.repo.rows) == 1


# ---------------- 11: config_version reproducibility ----------------


def test_config_version_reproducibility(import_ctx):
    a = _import(import_ctx.client).json()

    repo2 = FakeConfigRepo()
    svc2 = ConfigImportService(repo2, FakeProjectRepo(), default_config_service)
    resp2 = svc2.import_version(project_id="proj-2", domain="general", yaml_text=VALID_YAML)
    # same canonical effective configuration -> same hash (invariant 5)
    assert resp2[2]["config_version"] == a["config_version"]

    modified = VALID_YAML.replace("threshold: 0.9", "threshold: 0.95")
    b = _import(import_ctx.client, modified).json()
    assert b["config_version"] != a["config_version"]


# ---------------- 12: export/import roundtrip ----------------


def test_export_roundtrip(import_ctx):
    created = _import(import_ctx.client).json()
    resp = import_ctx.client.get(f"/api/configs/{created['config_id']}/yaml")
    assert resp.status_code == 200, resp.text
    exported = resp.json()
    assert exported["config_version"] == created["config_version"]
    assert exported["profile"] == "strict_financial"
    assert exported["version"] == "v1"

    # roundtrip: importing the exported text reproduces the identical body ->
    # dedupe returns the same row (imported body == stored body == exported)
    again = _import(import_ctx.client, exported["yaml"]).json()
    assert again["created"] is False
    assert again["config_id"] == created["config_id"]
    assert again["config_version"] == created["config_version"]


def test_config_detail_stored_and_pointer_rows(import_ctx):
    """§11 Profile Detail endpoint: stored rows resolve from the persisted body;
    pointer rows from the deployment YAML; config_version reports the STORED
    row value (historical fact, never recomputed)."""
    created = _import(import_ctx.client).json()
    d = import_ctx.client.get(f"/api/configs/{created['config_id']}").json()
    assert d["source"] == "imported"
    assert d["version"] == "v1"
    assert d["profile"] == "strict_financial"
    assert d["config_version"] == created["config_version"]
    assert d["yaml"] == VALID_YAML
    assert [m["name"] for m in d["metrics"]].count("faithfulness") == 1
    assert d["judge"]["temperature"] is not None  # public judge fields only

    row = import_ctx.repo.create_config(
        project_id=PROJECT, name="default", domain_config={"domain": "general"},
        profile_config={"profile": "default"}, pipeline_config=None, config_version="legacy",
    )
    dp = import_ctx.client.get(f"/api/configs/{row.id}").json()
    assert dp["source"] == "yaml_pointer"
    assert dp["version"] is None
    assert dp["yaml"] == default_config_service.raw_profile_yaml("default")
    assert dp["metrics"]  # metrics parsed from the deployment YAML


def test_export_pointer_row_reads_deployment_yaml(import_ctx):
    # legacy pointer row (thin profile_config): export falls back to the
    # deployment YAML file content — server-side, never frontend reassembly
    row = import_ctx.repo.create_config(
        project_id=PROJECT, name="default", domain_config={"domain": "general"},
        profile_config={"profile": "default"}, pipeline_config=None,
        config_version="legacy",
    )
    data = import_ctx.client.get(f"/api/configs/{row.id}/yaml").json()
    assert data["yaml"] == default_config_service.raw_profile_yaml("default")
    assert data["version"] is None


def test_list_project_configs(import_ctx):
    _import(import_ctx.client)
    import_ctx.repo.create_config(
        project_id=PROJECT, name="default", domain_config={"domain": "general"},
        profile_config={"profile": "default"}, pipeline_config=None, config_version="legacy",
    )
    data = import_ctx.client.get(f"/api/projects/{PROJECT}/configs").json()
    assert data["total"] == 2
    sources = {i["source"] for i in data["items"]}
    assert sources == {"imported", "yaml_pointer"}


# ---------------- execution chain uses the stored body (invariant 6, §14) ----------------


STORED_RUN_BODY = {
    "profile": {"name": "stored_strict"},
    "metrics": {
        "entity_consistency": {"enabled": True, "threshold": 0.85, "weight": 1.0},
        "numerical_consistency": {"enabled": True, "threshold": None, "weight": 2.0},
    },
    "severity_mapping": {"numerical_mismatch": "CRITICAL"},
}


@pytest.fixture()
def run_ctx():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "domains").mkdir(parents=True, exist_ok=True)
        (base / "evaluations").mkdir(parents=True, exist_ok=True)
        (base / "system.yaml").write_text("system:\n  judge:\n    model: test-judge\n", encoding="utf-8")
        (base / "domains/general.yaml").write_text("domain: general\n", encoding="utf-8")
        (base / "evaluations/default.yaml").write_text(
            "metrics:\n  entity_consistency: { enabled: true, threshold: null }\n", encoding="utf-8")
        config_service = ConfigService(base)

        stored_row = SimpleNamespace(
            id="c1", domain_config={"domain": "general"},
            profile_config={"profile": "stored_strict", "version": "v3",
                            "source": "imported", "yaml": "n/a", "body": STORED_RUN_BODY},
            config_version=config_service.config_version("general", "stored_strict",
                                                         profile_body=STORED_RUN_BODY),
        )
        repo = FakeEvaluationRepo(
            {"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}},
            [_record_row("r1", "q1", "2亿元", "3亿元", ["ref 1亿元"])],
            configs={"c1": stored_row},
        )

        class _Launcher:
            def __call__(self, run_id, *, enabled_metrics, params):
                async def _run():
                    await EvaluationService(repo).execute_run(
                        run_id, engines=[IntegrityEngine()], enabled_metrics=enabled_metrics,
                        params=params, runner=LocalAsyncRunner(concurrency=1))
                return asyncio.get_running_loop().create_task(_run())

        app.dependency_overrides[get_evaluation_service] = lambda: EvaluationService(repo)
        app.dependency_overrides[get_config_service] = lambda: config_service
        app.dependency_overrides[get_evaluation_launcher] = lambda: _Launcher()
        client = TestClient(app)
        yield SimpleNamespace(client=client, repo=repo, config_service=config_service)
        app.dependency_overrides.clear()


def _wait_terminal(repo, run_id, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = repo.runs[run_id]["status"]
        if status in ("completed", "completed_with_errors", "failed", "cancelled"):
            return status
        time.sleep(0.005)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


def _metric_rows(repo, run_id, name):
    """Join metric_results -> eval_results (fake rows carry result_id, not run_id)."""
    result_ids = {r["id"] for r in repo.eval_results if r["run_id"] == run_id}
    return [m for m in repo.metric_results
            if m["metric_name"] == name and m["result_id"] in result_ids]


def test_run_uses_stored_body_snapshot_and_override(run_ctx):
    resp = run_ctx.client.post("/api/evaluations", json={
        "project_id": "p1", "dataset_id": "ds1", "config_id": "c1",
        "metric_overrides": {"entity_consistency": {"threshold": 0.92}},
    })
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    meta = resp.json()["reproducibility_meta"]
    # snapshot: stored profile identity + effective (overridden) threshold
    assert meta["profile"] == "stored_strict"
    assert meta["profile_version"] == "v3"
    assert meta["effective_metrics"]["entity_consistency"]["threshold"] == 0.92
    assert meta["effective_metrics"]["numerical_consistency"]["threshold"] is None
    # config_version = sha256(canonical effective config via stored body)
    assert meta["config_version"] == run_ctx.config_service.config_version(
        "general", "stored_strict", profile_body=STORED_RUN_BODY)

    assert _wait_terminal(run_ctx.repo, run_id) == "completed"
    entity = _metric_rows(run_ctx.repo, run_id, "entity_consistency")
    assert entity and entity[0]["threshold"] == 0.92  # MetricResult carries the override


def test_run_without_override_uses_stored_threshold(run_ctx):
    resp = run_ctx.client.post("/api/evaluations", json={
        "project_id": "p1", "dataset_id": "ds1", "config_id": "c1"})
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    _wait_terminal(run_ctx.repo, run_id)
    entity = _metric_rows(run_ctx.repo, run_id, "entity_consistency")
    assert entity[0]["threshold"] == 0.85  # stored body threshold, NOT the YAML file's null


# ---------------- invariant 3: quality gate resolves through stored body ----------------


def test_quality_gate_config_from_stored_body():
    from app.services.quality_gate_service import QualityGateService

    body = {**STORED_RUN_BODY,
            "quality_gate": {"enabled": True, "required_metrics": ["entity_consistency"]}}
    row = SimpleNamespace(
        id="c1", domain_config={"domain": "general"},
        profile_config={"profile": "stored_strict", "version": "v1",
                        "source": "imported", "yaml": "n/a", "body": body},
        config_version="x",
    )
    run = SimpleNamespace(id="r1", config_id="c1", reproducibility_meta={})

    class _Repo:
        def get_config(self, config_id):
            return row

    svc = QualityGateService(_Repo(), default_config_service)
    assert svc._quality_gate_config(run) == body["quality_gate"]
