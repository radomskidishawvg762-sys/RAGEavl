from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.schemas import DatasetRecordData
from app.models import Dataset, DatasetRecord
from app.repositories.base import BaseRepository


class DatasetRepository(BaseRepository[Dataset]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Dataset)

    def get(self, dataset_id: str) -> Dataset | None:
        return self._session.get(Dataset, dataset_id)

    def get_max_version(self, project_id: str, name: str) -> int | None:
        """Highest existing numeric version for (project_id, name), e.g. 'v3' -> 3."""
        stmt = select(Dataset.version).where(
            Dataset.project_id == project_id, Dataset.name == name
        )
        rows = self._session.execute(stmt).all()
        nums = [int(r[0][1:]) for r in rows if isinstance(r[0], str) and r[0][:1] == "v" and r[0][1:].isdigit()]
        return max(nums) if nums else None

    def create_version_with_records(
        self,
        project_id: str,
        name: str,
        *,
        record_count: int,
        validation_status: str,
        validation_report: dict,
        records: list[DatasetRecordData],
    ) -> Dataset:
        """One transaction (Spec T-07: 版本化 + 事务内一致性):
        next version -> insert dataset -> bulk insert records -> commit.

        `record_count` is the parsed-record count (Spec UI mock shows
        '300 Invalid' with count); `records` are only persisted for valid
        versions — dirty data never enters a dataset version.
        """
        last = self.get_max_version(project_id, name)
        ds = Dataset(
            project_id=project_id,
            name=name,
            version=f"v{(last or 0) + 1}",
            record_count=record_count,
            validation_status=validation_status,
            validation_report=validation_report,
            is_locked=False,
        )
        self._session.add(ds)
        self._session.flush()
        if records:
            self._session.add_all(
                DatasetRecord(
                    dataset_id=ds.id,
                    row_index=i,
                    question=r.question,
                    reference_answer=r.reference_answer,
                    reference_contexts=r.reference_contexts,
                    metadata_=r.metadata,
                )
                for i, r in enumerate(records)
            )
        self._session.commit()
        return ds

    def list_page(
        self,
        *,
        page: int,
        page_size: int,
        project_id: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[Dataset], int]:
        columns = {
            "id": Dataset.id,
            "project_id": Dataset.project_id,
            "name": Dataset.name,
            "version": Dataset.version,
            "record_count": Dataset.record_count,
            "validation_status": Dataset.validation_status,
            "is_locked": Dataset.is_locked,
            "created_at": Dataset.created_at,
        }
        column = columns.get(sort)
        if column is None or order not in {"asc", "desc"}:
            raise ValueError("unsupported dataset sort or order")
        base = select(Dataset)
        if project_id is not None:
            base = base.where(Dataset.project_id == project_id)
        total = int(self._session.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one())
        primary = column.asc() if order == "asc" else column.desc()
        tie_breaker = Dataset.id.asc() if order == "asc" else Dataset.id.desc()
        rows = self._session.execute(
            base.order_by(primary, tie_breaker)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
        return list(rows), total

    def list_records_page(
        self, dataset_id: str, page: int, page_size: int
    ) -> tuple[list[DatasetRecord], int]:
        base = select(DatasetRecord).where(DatasetRecord.dataset_id == dataset_id)
        total = self._session.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()
        rows = self._session.execute(
            base.order_by(DatasetRecord.row_index).offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()
        return list(rows), int(total)
