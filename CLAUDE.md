# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

**MVP is implemented and past its Freeze Gate** (2026-09-01; status re-audit 2026-09-04 in `docs/releases/MVP_FINAL_ACCEPTANCE.md` §0). The full layered backend (`app/`: api / services / repositories / domain / models / db / runner / engines / metrics / diagnosis / core / adapters), Alembic migrations (3 heads-locked revisions), React frontend (`frontend/`), and the test suite (`tests/`, 400+ tests) all exist. Documents: `RAGEval_Studio_PRD_v1.0.md` (PRD v1.3), `RAGEval_Studio_Spec_v1.0.md` (internally **spec v2.1** — the design source of truth), `README.md`, `docs/deployment/SUPABASE_CLOUD.md`, `docs/releases/MVP_FINAL_ACCEPTANCE.md`. PostgreSQL integration tests require a dedicated `TEST_DATABASE_URL` (skipped honestly when unset — never counted as passed). When changing code, follow the layout and ADRs below verbatim — do not improvise the architecture.

RAGEval Studio is a RAG quality evaluation & diagnosis platform: `Evaluation → Failure Detection → Diagnosis → Evidence → Root Cause → Recommendation → Version Comparison → Regression`. It tells the user not just the score but *why* it failed, *on what evidence*, and *whether a fix actually improved things*.

## Commands

```bash
# Backend
alembic upgrade head            # ONLY way to create tables — never use Supabase Dashboard
alembic current                 # check migration state
uvicorn app.main:app --reload    # local dev server
docker compose up --build       # run only the app container (no db service)

# Frontend
cd frontend && npm install && npm run dev

# Tests (PostgreSQL integration tests auto-skip without TEST_DATABASE_URL; see README)
pytest
pytest tests/test_x.py::test_name     # single test

# Health check
curl http://localhost:8000/api/health   # → {app, database, detail.db_latency_ms}
```

Environment: `cp .env.example .env.local`. `RAGEVAL_ENV` = `dev | test | demo` (controls log level + CORS).

## Architecture

Layered monolith + pluggable engine registry. Mandatory access chain:

```
FastAPI → Service → Repository → SQLAlchemy 2.0 → psycopg[binary] → PostgreSQL 17 (Supabase Cloud, hosting only)
```

Planned layout (spec Appendix F — `backend/app/`):

| Dir | Role |
|-----|------|
| `api/` | Routers per resource (projects, datasets, evaluations, reports, comparisons, configs) |
| `services/` | Business orchestration (`<x>_service`). Never touches Session/ORM directly. |
| `domain/` | Pydantic schemas (`schemas.py`), `invariants.py`. |
| `repositories/` | **Sole data-access boundary** (ORM queries + transactions). |
| `models/` | SQLAlchemy declarative models. |
| `db/` | `session.py` (Engine + SessionLocal + pool), `base.py` (DeclarativeBase), `health.py` (SELECT 1). |
| `runner/` | `EvaluationRunner` protocol + `LocalAsyncRunner` (MVP). |
| `engines/` | `EvaluationEngine` Protocol + `ragas.py` / `integrity.py` / `composite.py`. |
| `metrics/` | `registry.py` + retrieval/generation/entity/temporal/numerical. |
| `diagnosis/` | `engine.py` · `taxonomy.py` · `rules.py` · `evidence.py` · `recommendations.py`. |
| `core/` | config / logging / errors. |

Layering rule (spec 3.3): `API → Service → Domain → (Engine/Diagnosis impl)`; `Repository → db/session → SQLAlchemy → PostgreSQL`. Forbidden directions: Engine→Service, Domain→FastAPI, Engine A→Engine B, Service→Session/ORM, Service→supabase SDK.

Naming: API + DB are `snake_case`; the frontend TS layer maps to `camelCase`.

### Three-layer config (spec Appendix B.3)

Priority (later overrides earlier): `config/system.yaml < config/domains/<domain>.yaml < config/evaluations/<profile>.yaml < Run-level overrides`. YAML, loaded/merged by `ConfigService`. Mounted read-only into the app container. No runtime hot reload (ADR-05).

- `system.yaml`: `system.database.url: ${RAGEVAL_DB_URL}`, `system.judge`, `system.logging` (with `redact_fields`), `system.limits.max_records_per_dataset: 1000`.
- Domain YAML only *recommends* metrics (`recommended_metrics`), **never thresholds**.
- Profile YAML: `metrics.<name>.{enabled,threshold,weight}`. `threshold: null` = no PASS/FAIL judgment.
- On Run creation, snapshot `config_version = sha256(yaml_dump(merged config))`.

### Evaluation pipeline & 10 tables

Domain chain: `EvaluationRecord → EvaluationRun → MetricResult → Failure → Evidence → Diagnosis → Recommendation → Regression`. The 10 Alembic-managed tables:

```
projects · datasets · dataset_records · evaluation_configs · evaluation_runs
evaluation_results · metric_results · diagnoses · recommendations · metric_definitions
```

`POST /api/evaluations` flow (spec 7.1): single transaction — `SELECT ... FOR UPDATE` on the dataset row → verify `is_locked == false` → insert run (`status=pending` + 8 reproducibility fields) → set `datasets.is_locked = true` → COMMIT → 202 `{run_id, status, reproducibility_meta}` → `EvaluationRunner` per-record loop with error isolation (single record failure → `metric_results.error`, `error_records++`, batch continues) → terminal status → `diagnose()` (RuleEngine match → EvidenceCollector per contract → contract satisfied ? persist Diagnosis + Recommendations : persist `undetermined`).

Run states: `pending, running, completed, completed_with_errors, failed, cancelled` (no percentage thresholds). The 8 mandatory reproducibility fields: `dataset_version, config_version, metric_version, prompt_version, judge_model, judge_model_version, model_version, timestamp`.

REST: prefix `/api`; pagination `?page=1&page_size=20` → `{items, total, page, page_size}`; error codes segmented `BIZ_` / `SYS_` / `EXT_` (e.g. `BIZ_DATASET_LOCKED` 409, `SYS_INSUFFICIENT_EVIDENCE` 500, `EXT_DB_UNAVAILABLE` 503); unified body `{detail, code, trace_id, context}`.

### Metrics & engines

- **RagasEngine** (P0): faithfulness, answer_relevancy, context_recall, context_precision. RAGAS version locked and recorded in `metric_version`.
- **IntegrityEngine** (P0): `entity_consistency`, `temporal_consistency`, `numerical_consistency` via **Deterministic-first Hybrid** (ADR-04): Extract → Normalize (aliases/units/date parsing) → Deterministic Compare → Tolerance/Equivalence → **LLM fallback only when inconclusive** → MetricResult must carry `comparison_basis`.
- **CompositeEngine**: weighted `overall_score` from Profile. `ArgusEngine` is P1.
- Engines register via `EvaluationEngine` Protocol + `MetricRegistry` (`register_metric`/`get_metric`/`list_metrics`). The business layer must **never branch on engine name** (`if engine == "ragas"` is forbidden — CI static check). `metric_definitions` table is a read-only mirror of code-registered metrics for UI/snapshot — **not** a registration entry point.
- Judge LLM (LLM-as-a-Judge): single fixed model (MVP), `temperature=0`, `retry=3`, `model_version` recorded, SDK isolated in `JudgeClient`. When deterministic judgment is inconclusive and Judge is unavailable, output `comparison_type: "ambiguous"`, `score: null`, `passed: null` — **never fabricate a plausible score**; run marks `completed_with_errors`.
- Failure Taxonomy: 14 codes — `retrieval.*` (7), `generation.*` (3), `integrity.*` (4). `Financial Hallucination` is **not** a metric; it's the collective of `integrity.*` + `generation.unsupported_claim`.

### Frontend

React + TypeScript + Ant Design + ECharts + Axios (Vite, target `es2020`). `frontend/src/` → `pages/ · components/ · api/ · types/`. Routes: `/`, `/datasets/:id`, `/evaluations/new`, `/evaluations/:id`, `/comparisons`. TS client maps snake_case→camelCase. **Frontend must not hardcode any default thresholds/severities** — render only from the active Profile. Progress polling: while `status ∈ {pending, running}`, poll `/api/evaluations/{id}/progress` every 2s, stop at terminal state and fetch full report.

## Hard constraints (spec-mandated, non-negotiable)

These are deliberate design rules — violating them breaks invariants the test suite enforces.

- **Supabase is hosting only.** No `supabase-py`/`supabase-js`/Auth/Storage/Realtime/Self-Hosted. No `app/services/supabase_service.py`. DB code lives only in `db/`, `repositories/`, `configuration/`. CI checks: no supabase import under `api/`, `services/`, `domain/` (invariant I-8).
- **No Redis / Celery / ARQ / Kafka / Kubernetes / OpenTelemetry SDK** in MVP. Async work uses FastAPI `BackgroundTasks` + asyncio; an in-process `asyncio.Lock` serializes runs (ADR-02).
- **Alembic is the only migration system.** Never create tables in Supabase Dashboard, never `supabase db push`, never `Drop Database → Create Again`. Never touch Supabase platform schemas (`auth`, `storage`, `realtime`, `supabase_*`).
- **Repository is the sole data-access boundary.** Service layer never uses `Session` / `session.query(...)` directly (P-8). No implicit autocommit — explicit transaction boundaries.
- **No `localhost:5432` / `127.0.0.1:5432` as a production default**, no db container, no `pg_isready`/`wait-for-postgres`/`pgdata`/`depends_on: db`. docker-compose has only the `app` service.
- **`TEST_DATABASE_URL` must differ from `DATABASE_URL`** — `conftest.py` hard-fails (not warns) if they match. Tests must not depend on localhost:5432 or a compose db service.
- **Secrets** (`DATABASE_URL`, Judge API key) never in YAML/DB/reports/logs/Git. Use `Pydantic SecretStr` + a log-redact filter. `.env.local`/`.env.test` are gitignored; `.env.example` holds placeholders only. Connection string format stays `postgresql+psycopg://<user>:<password>@<host>:<port>/<database>` (do not switch drivers).
- **Diagnosis is rules-first, LLM-fallback only.** No root-cause from score alone — must go through the Evidence contract path, else `InsufficientEvidenceError`. Evidence JSON must be `{contract: "<type>.v1", items: [{type, source, locator, content}]}` — flat string arrays are forbidden. No `evidence.length >= 1` blanket rules.
- **No metric generates Diagnosis internally** (P-1). Integrity metrics must fill `comparison_basis`; temporal must parse to intervals (no raw string compare); numeric must extract → unit-normalize → tolerance (no string equality).
- **No platform default thresholds/severities** in code or frontend. `threshold: null` means no PASS/FAIL judgment. The platform ships zero built-in industry defaults (PRD Appendix B).
- **Standard PostgreSQL only** (P-9). JSONB, partial indexes, CTE, window functions are fine. No DB `enum` types — use TEXT + app-layer validation. No string-concatenated SQL — parameterized only. Don't hardcode the PostgreSQL version in app code. Engine pool params fixed: `pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=1800`.

## Testing (spec Appendix E)

pytest + pytest-asyncio. `conftest.py` must assert `TEST_DATABASE_URL != DATABASE_URL` (hard fail). Migration test flow (E.5, mandatory): empty DB → `alembic upgrade head` → verify all 10 business tables → basic CRUD → Evaluation Run end-to-end → rollback/downgrade (dev only). Invariant tests I-1..I-8 enforce: locked-dataset write → 409 `BIZ_DATASET_LOCKED`; concurrent runs on one dataset → exactly one wins (row-lock race); `metric_results` row count = records × enabled metrics; missing evidence → `InsufficientEvidenceError`; score-only diagnosis rejected; 1000 records with 3 injected failures → `completed_with_errors`; RecommendationBuilder makes no external writes; 8 reproducibility fields non-null; no supabase import in `api/`/`services/`/`domain/`. A log-secret test asserts `api_key`/`authorization`/connection-string password never appear in logs.

## ADRs (spec §9)

- **ADR-01** Layered monolith, not microservices (MVP local single-user tool).
- **ADR-02** No Redis/MQ in MVP — `BackgroundTasks` + asyncio; in-process `asyncio.Lock` serializes runs.
- **ADR-03** Engine abstraction via Protocol + Registry; no branching on engine names.
- **ADR-04** Diagnosis = rules-first + LLM fallback; inconclusive → `ambiguous`, never guesses.
- **ADR-05** Three-layer YAML config + content-hash snapshot; no DB config tables, no hot reload.
- **ADR-06** Dataset versions immutable — `is_locked` is a hard constraint; fixes require a new version.
- **ADR-07** Evidence contracts validated per diagnosis type; insufficient evidence → `undetermined`.
- **ADR-08** DB hosting on Supabase Cloud; access layer stays SQLAlchemy + Alembic; provider swap = change `DATABASE_URL` only.
- **ADR-09** `EvaluationRunner` execution abstraction (`LocalAsyncRunner` MVP, `QueueRunner` P1) so a persistent queue can be added without touching Service.
