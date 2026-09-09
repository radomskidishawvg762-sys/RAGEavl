from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Project
from app.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Project)

    def get(self, project_id: str) -> Project | None:
        return self._session.get(Project, project_id)

    def get_by_name(self, name: str) -> Project | None:
        """Name-uniqueness probe (FR-01): matches active AND archived rows so
        an archived project's name stays reserved."""
        return self._session.execute(
            select(Project).where(Project.name == name)
        ).scalar_one_or_none()

    def create(self, *, name: str, domain: str, status: str = "active") -> Project:
        """Insert one project row. Caller (Service) owns validation; the
        repository owns persistence only."""
        project = Project(name=name, domain=domain, status=status)
        self._session.add(project)
        self._session.commit()
        return project

    def list_page(
        self,
        *,
        page: int,
        page_size: int,
        sort: str = "created_at",
        order: str = "desc",
        status: str | None = None,
    ) -> tuple[list[Project], int]:
        columns = {
            "id": Project.id,
            "name": Project.name,
            "domain": Project.domain,
            "status": Project.status,
            "created_at": Project.created_at,
        }
        column = columns.get(sort)
        if column is None or order not in {"asc", "desc"}:
            raise ValueError("unsupported project sort or order")
        base = select(Project)
        if status is not None:
            base = base.where(Project.status == status)
        total = int(self._session.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one())
        primary = column.asc() if order == "asc" else column.desc()
        tie_breaker = Project.id.asc() if order == "asc" else Project.id.desc()
        rows = self._session.execute(
            base.order_by(primary, tie_breaker)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
        return list(rows), total
