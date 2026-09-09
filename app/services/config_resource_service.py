"""ConfigResourceService (T-18) — persist a saved evaluation config (config_id).

POST /api/projects/{pid}/configs (spec Appendix C / FR-11/12): converts the
frontend's Profile selection into an `evaluation_configs` row whose
domain_config/profile_config point at the three-layer YAML profile. The POST
/api/evaluations handler resolves domain/profile from that row and re-loads the
YAML via ConfigService — the config row is a thin pointer, never a second
config source.

Additive to the frozen evaluation flow: `EvaluationService.create_run` /
`execute_run` are untouched; this service only writes a config row via the repo.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ConfigInvalidError, NotFoundError
from app.models import EvaluationConfig
from app.repositories.evaluation import EvaluationRepository
from app.repositories.project import ProjectRepository
from app.services.config_service import ConfigService, default_config_service


def resolve_config_row(cfg: EvaluationConfig) -> tuple[str, str, dict | None]:
    """Unified resolution of an evaluation_configs row (Phase B invariant 1/4).

    Returns (domain, profile, profile_body):
      pointer row (legacy)  profile_config={"profile": n}      -> body=None
                            (executor re-loads the immutable deployment YAML)
      stored row (imported) profile_config={"profile", "version",
                            "source": "imported", "yaml", "body"} -> body=<dict>

    The stored body is returned AS PERSISTED — never re-read from YAML, never
    regenerated. Callers must treat (config_id -> body) as stable: content
    changes can only ever live in a NEW config row (invariant 4).
    """
    domain = (cfg.domain_config or {}).get("domain", "general")
    pc = cfg.profile_config if isinstance(cfg.profile_config, dict) else {}
    profile = str(pc.get("profile", "default"))
    body = pc.get("body") if pc.get("source") == "imported" else None
    return domain, profile, body if isinstance(body, dict) else None


class ConfigResourceService:
    def __init__(
        self,
        repo: EvaluationRepository,
        project_repo: ProjectRepository,
        config_service: ConfigService | None = None,
    ) -> None:
        self._repo = repo
        self._project_repo = project_repo
        self._cfg = config_service or default_config_service

    def save(
        self,
        *,
        project_id: str,
        name: str,
        domain: str = "general",
        profile: str,
        pipeline_config: dict[str, Any] | None = None,
    ):
        """Create (or idempotently reuse) a saved config row.

        Returns (row, created, config_version):
          row            EvaluationConfig
          created        True when a new row was inserted; False on reuse
          config_version sha256 of the token (pre-resolution) merged config
        Raises:
          NotFoundError (404)       — unknown project
          ConfigInvalidError (409)  — unknown evaluation profile
        """
        if self._project_repo.get(project_id) is None:
            raise NotFoundError(f"project {project_id} not found")
        if not self._cfg.profile_exists(profile):
            raise ConfigInvalidError(f"unknown evaluation profile: {profile}")

        config_version = self._cfg.config_version(domain, profile)
        existing = self._repo.get_config_by_name(project_id, name)
        if existing is not None:
            return existing, False, config_version

        row = self._repo.create_config(
            project_id=project_id,
            name=name,
            domain_config={"domain": domain},
            profile_config={"profile": profile},
            pipeline_config=pipeline_config,
            config_version=config_version,
        )
        return row, True, config_version
