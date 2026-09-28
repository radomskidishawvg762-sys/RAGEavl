from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.errors import BizError, NotFoundError
from app.datasets import validation
from app.datasets.adapters import parse_import_payload, to_dataset_records
from app.domain.schemas import DatasetRecordData
from app.metrics.registry import default_registry
from app.models import Dataset, DatasetRecord
from app.repositories.dataset import DatasetRepository
from app.repositories.project import ProjectRepository

logger = logging.getLogger(__name__)


class DatasetService:
    """Import + 5-check validation + versioning (FR-03~06, Spec §3.2).

    Import is one of the 4 stages with mandatory start/end logging (Spec §8.5).
    Content is never logged (Level A redaction) — only counts and ids.
    """

    def __init__(
        self,
        session: Session,
        max_records: int = 1000,
        *,
        project_repo: ProjectRepository | None = None,
        dataset_repo: DatasetRepository | None = None,
    ) -> None:
        # repo params are a test seam (fake repositories); production wiring
        # stays API -> Service -> Repository with real session-scoped repos.
        self._projects = project_repo or ProjectRepository(session)
        self._datasets = dataset_repo or DatasetRepository(session)
        self._max_records = max_records

    def _required_fields(self) -> list[str]:
        """Union of input_requirements across registered metrics (Spec A.4 check #3),
        intersected with golden-set fields.

        Runtime-only fields (answer/contexts) arrive from the RAG system at
        evaluation time and are NOT import-checkable — dataset_records has no
        such columns (Spec §5.2). Registry-driven; no hardcoded defaults here.
        """
        golden_fields = set(DatasetRecordData.model_fields)
        reqs: set[str] = set()
        for spec in default_registry.list_metrics():
            reqs.update(spec.input_requirements)
        return sorted(r for r in reqs if r.split(".")[0] in golden_fields)

    def import_dataset(self, project_id: str, raw: bytes) -> Dataset:
        """Parse -> validate -> persist. Returns the persisted Dataset row.

        Parse error  -> BIZ_ADAPTER_PARSE_ERROR 400, nothing persisted.
        Check failed -> dataset persisted as invalid (no records), then
                        BIZ_VALIDATION_FAILED 400 with the report in context
                        (Spec §6.5: 展示 validation_report).
        Success      -> valid dataset + records, single transaction.
        """
        if self._projects.get(project_id) is None:
            raise NotFoundError(f"project {project_id} not found", context={"project_id": project_id})

        logger.info("dataset import start: project_id=%s bytes=%d", project_id, len(raw))
        name, domain, policy, raw_records = parse_import_payload(raw, self._max_records)

        report, drop = validation.validate_records(
            raw_records, domain=domain, required_fields=self._required_fields(), duplicate_policy=policy
        )
        kept: list[dict] = [r for i, r in enumerate(raw_records) if i not in set(drop)]
        # Conversion only for valid reports: schema-failed records may lack
        # required fields, so converting them would mask the report as a parse error.
        records: list[DatasetRecordData] = to_dataset_records(kept) if report.valid else []

        status = "valid" if report.valid else "invalid"
        # record_count describes what was PERSISTED (the deduplicated subset), not
        # what was parsed. An INVALID import deliberately keeps the SUBMITTED count
        # instead — asserted by test_import_validation_failed_400_….
        record_count = len(kept) if report.valid else len(raw_records)
        ds = self._datasets.create_version_with_records(
            project_id,
            name,
            record_count=record_count,
            validation_status=status,
            validation_report=report.model_dump(),
            records=records if report.valid else [],
        )
        logger.info(
            "dataset import end: dataset_id=%s version=%s status=%s records=%d issues=%d",
            ds.id,
            ds.version,
            status,
            len(raw_records),
            sum(len(c.issues) for c in report.checks),
        )
        if not report.valid:
            raise BizError(
                "dataset validation failed",
                code="BIZ_VALIDATION_FAILED",
                http_status=400,
                context={"dataset_id": ds.id, "version": ds.version, "validation_report": report.model_dump()},
            )
        return ds

    def get(self, dataset_id: str) -> Dataset:
        ds = self._datasets.get(dataset_id)
        if ds is None:
            raise NotFoundError(f"dataset {dataset_id} not found", context={"dataset_id": dataset_id})
        return ds

    def validation_report(self, dataset_id: str) -> dict:
        ds = self.get(dataset_id)
        return {
            "dataset_id": ds.id,
            "validation_status": ds.validation_status,
            "validation_report": ds.validation_report or {},
        }

    def list_datasets(
        self,
        *,
        page: int,
        page_size: int,
        project_id: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ):
        return self._datasets.list_page(
            page=page,
            page_size=page_size,
            project_id=project_id,
            sort=sort,
            order=order,
        )

    def list_records(self, dataset_id: str, page: int, page_size: int) -> tuple[list[DatasetRecord], int]:
        self.get(dataset_id)  # 404 guard
        return self._datasets.list_records_page(dataset_id, page, page_size)
