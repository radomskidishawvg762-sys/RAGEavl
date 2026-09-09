from __future__ import annotations

from app.core.errors import BizError, ProjectNameExistsError
from app.repositories.project import ProjectRepository

_MAX_NAME_LEN = 200


class ProjectService:
    """Project resource service (FR-01/FR-02).

    create_project validation chain (Phase 1A G1):
      name      -> strip, non-empty, length-bounded (else BIZ_VALIDATION_FAILED 400)
      domain    -> must exist as a layer-2 config/domains/<domain>.yaml
                   (else BIZ_VALIDATION_FAILED 400) — a project pointing at a
                   domain without YAML would silently lose its domain layer at
                   run time (ConfigService.load)
      duplicate -> name already used by ANY row (active or archived)
                   (else BIZ_PROJECT_NAME_EXISTS 409)
      status    -> default "active" (model contract: active|archived)
    """

    def __init__(
        self,
        repo: ProjectRepository,
        *,
        valid_domains: list[str] | None = None,
    ) -> None:
        self._repo = repo
        self._valid_domains = frozenset(valid_domains) if valid_domains is not None else None

    def list_projects(
        self,
        *,
        page: int,
        page_size: int,
        sort: str = "created_at",
        order: str = "desc",
        status: str | None = None,
    ):
        return self._repo.list_page(
            page=page,
            page_size=page_size,
            sort=sort,
            order=order,
            status=status,
        )

    def create_project(self, *, name: str, domain: str = "general", status: str = "active"):
        clean = (name or "").strip()
        if not clean:
            raise BizError("project name must not be empty")
        if len(clean) > _MAX_NAME_LEN:
            raise BizError(f"project name exceeds {_MAX_NAME_LEN} characters")
        if self._valid_domains is not None and domain not in self._valid_domains:
            raise BizError(
                f"unknown domain '{domain}'; available: {sorted(self._valid_domains)}"
            )
        if self._repo.get_by_name(clean) is not None:
            raise ProjectNameExistsError(f"project name '{clean}' already exists")
        return self._repo.create(name=clean, domain=domain, status=status)
