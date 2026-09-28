from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters.rag_input import build_rag_adapter
from app.core.errors import SysError
from app.db.session import get_session, open_session
from app.engines.factory import build_engines
from app.repositories.evaluation import EvaluationRepository
from app.repositories.project import ProjectRepository
from app.repositories.project_stats import ProjectStatsRepository
from app.services.catalog_service import CatalogService
from app.services.comparison_service import ComparisonService
from app.services.config_import_service import ConfigImportService
from app.services.config_resource_service import ConfigResourceService
from app.services.config_service import ConfigService, default_config_service
from app.services.dataset_service import DatasetService
from app.services.evaluation_service import EvaluationService
from app.services.judge_settings_service import JudgeSettingsService, default_judge_settings_service
from app.services.project_service import ProjectService
from app.services.project_workspace_service import ProjectWorkspaceService
from app.services.quality_gate_service import QualityGateService
from app.services.regression_service import RegressionService, resolve_epsilon
from app.services.report_service import ReportService

logger = logging.getLogger(__name__)


def get_dataset_service(session: Session = Depends(get_session)) -> DatasetService:
    merged = default_config_service.load()
    limits = (merged.get("system") or {}).get("limits") or {}
    return DatasetService(session, max_records=int(limits.get("max_records_per_dataset", 1000)))


def get_project_service(session: Session = Depends(get_session)) -> ProjectService:
    return ProjectService(
        ProjectRepository(session),
        valid_domains=default_config_service.domain_names(),
    )


def get_project_workspace_service(session: Session = Depends(get_session)) -> ProjectWorkspaceService:
    return ProjectWorkspaceService(
        ProjectRepository(session),
        ProjectStatsRepository(session),
        QualityGateService(EvaluationRepository(session), default_config_service),
    )


def get_evaluation_service(session: Session = Depends(get_session)) -> EvaluationService:
    return EvaluationService(EvaluationRepository(session))


def get_config_service() -> ConfigService:
    return default_config_service


def get_catalog_service() -> CatalogService:
    """Read-only catalog (profiles + registered metrics). No Session — works even
    when the database is down, since it reads YAML + in-memory registry only."""
    return CatalogService()


def get_config_resource_service(
    session: Session = Depends(get_session),
) -> ConfigResourceService:
    return ConfigResourceService(
        EvaluationRepository(session), ProjectRepository(session), default_config_service
    )


def get_config_import_service(
    session: Session = Depends(get_session),
) -> ConfigImportService:
    """Configuration Lifecycle v1 (Phase B): import/preview/export/list."""
    return ConfigImportService(
        EvaluationRepository(session), ProjectRepository(session), default_config_service
    )


def get_judge_settings_service() -> JudgeSettingsService:
    return default_judge_settings_service


class EvaluationLauncher(Protocol):
    """Fire-and-forget run starter (T-13 §三): returns a task handle, never
    blocks the request until completion. Engines are built HERE (not in the
    Router — Router never touches the Engine layer)."""

    def __call__(
        self,
        run_id: str,
        *,
        enabled_metrics: list[str],
        params,
    ) -> Awaitable: ...


# Strong references to in-flight run tasks. asyncio keeps only a WEAK reference to
# a task, so a dropped return value lets a GC pass close the coroutine at an
# arbitrary await — the run row then stays "running" forever, with no error_summary
# and no log line. Entries are discarded as soon as the task finishes.
_launched_tasks: set[asyncio.Task] = set()


def _default_launcher() -> Callable[..., asyncio.Task]:
    """Real path: build engines from the profile, then execute_run on a
    DEDICATED session in a background task
    (API -> Service -> Runner -> Pipeline -> Engine). The request's own session
    is never reused — it dies with the request scope."""

    async def _background(run_id: str, *, enabled_metrics, params) -> None:
        try:
            engines, skipped = build_engines(
                enabled_metrics,
                alias_table=params.extra.get("alias_table"),
                judge_config=params.extra.get("judge_config"),  # public fields only
            )
            if skipped:
                # build_engines documents "a metric is NEVER silently dropped".
                # Plan time already rejects metrics the REGISTRY does not know
                # (run_planner.resolve_profile -> 409 BIZ_CONFIG_INVALID); this is
                # the other skip path: a registered metric whose ENGINE has no
                # builder. Raising lands the run in `failed` with a structured
                # error_summary via the except below, instead of completing with
                # those metrics simply absent from the report.
                raise SysError(
                    f"engine assembly dropped enabled metrics: {skipped}",
                    code="SYS_METRIC_BACKEND_UNAVAILABLE",
                    context={"metrics": skipped},
                )
            # T-14B formal input path: url configured -> HttpRagAdapter;
            # absent -> explicit GoldenRunMetadataAdapter (test/golden-run only)
            rag_adapter = build_rag_adapter(params.extra.get("rag_input"))
            with open_session() as session:
                svc = EvaluationService(EvaluationRepository(session))
                await svc.execute_run(
                    run_id, engines=engines, enabled_metrics=enabled_metrics, params=params,
                    rag_adapter=rag_adapter,
                    is_cancelled=lambda: svc.is_run_cancelled(run_id),
                )
        except Exception as e:  # noqa: BLE001 — background crash must not vanish silently
            logger.exception("background run %s crashed", run_id)
            try:
                with open_session() as session:
                    EvaluationService(EvaluationRepository(session)).mark_failed(run_id, e)
            except Exception:  # noqa: BLE001 — best-effort failure marking
                logger.exception("failed to mark run %s as failed", run_id)

    def launch(run_id: str, *, enabled_metrics, params) -> asyncio.Task:
        task = asyncio.create_task(
            _background(run_id, enabled_metrics=enabled_metrics, params=params)
        )
        _launched_tasks.add(task)  # see _launched_tasks — the loop holds only a weak ref
        task.add_done_callback(_launched_tasks.discard)
        return task

    return launch


def get_evaluation_launcher() -> Callable[..., asyncio.Task]:
    return _default_launcher()


def get_report_service(session: Session = Depends(get_session)) -> ReportService:
    return ReportService(EvaluationRepository(session))


def get_export_service(session: Session = Depends(get_session)):
    from app.services.export_service import ExportService

    repo = EvaluationRepository(session)
    return ExportService(repo, ReportService(repo))


def get_comparison_service(session: Session = Depends(get_session)) -> ComparisonService:
    return ComparisonService(EvaluationRepository(session))


def get_regression_service(session: Session = Depends(get_session)) -> RegressionService:
    merged = default_config_service.load()
    epsilon, source = resolve_epsilon(merged)
    return RegressionService(
        ComparisonService(EvaluationRepository(session)),
        epsilon=epsilon,
        epsilon_source=source,
    )


def get_quality_gate_service(session: Session = Depends(get_session)) -> QualityGateService:
    return QualityGateService(EvaluationRepository(session), default_config_service)
