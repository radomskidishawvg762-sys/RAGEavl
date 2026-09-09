"""CatalogService (T-18) — read-only catalog for the New Evaluation wizard.

Router -> CatalogService -> ConfigService / MetricRegistry (goal #9 thin router).
No ORM, no Session, no Engine, no Diagnosis — profiles/metrics come straight from
the SAME source the run planner uses (three-layer YAML + code-registered metrics),
so the frontend never carries a second config/metric source (goal #3).

Security: never serializes secrets. The merged config resolves system.database.url
to the real connection string in-memory; only the wizard-relevant SAFE fields are
extracted here (profile name/version, metrics, severity_mapping, quality_gate,
rag_input url/timeout/retry). judge api_key / database url never leave.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import NotFoundError
from app.metrics.registry import MetricRegistry, default_registry
from app.services.config_service import ConfigService, default_config_service


class CatalogService:
    def __init__(
        self,
        config_service: ConfigService | None = None,
        registry: MetricRegistry | None = None,
    ) -> None:
        self._cfg = config_service or default_config_service
        self._registry = registry or default_registry

    # ---- profiles ----

    def list_profiles(self, domain: str = "general") -> list[dict[str, Any]]:
        return [self._profile_dict(name, domain) for name in self._cfg.profile_names()]

    def get_profile(self, name: str, domain: str = "general") -> dict[str, Any]:
        if not self._cfg.profile_exists(name):
            raise NotFoundError(f"evaluation profile '{name}' not found")
        return self._profile_dict(name, domain)

    def _profile_dict(self, name: str, domain: str) -> dict[str, Any]:
        merged = self._cfg.load(domain, name)
        profile = merged.get("profile")
        profile = profile if isinstance(profile, dict) else {}
        metrics = merged.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        severity = merged.get("severity_mapping")
        severity = severity if isinstance(severity, dict) else {}
        # Phase D: effective rag_input (mode-aware), public judge fields and the
        # pipeline section — Review-page display inputs only, zero secret fields.
        from app.services.run_planner import normalize_rag_input

        system = merged.get("system")
        judge_src = system.get("judge") if isinstance(system.get("judge"), dict) else {}
        return {
            "name": str(profile.get("name", name)),
            "version": str(profile.get("version", "")),
            "domain": domain,
            "metrics": [
                self._profile_metric(mname, mcfg)
                for mname, mcfg in sorted(metrics.items())
            ],
            "severity_mapping": {str(k): str(v) for k, v in severity.items()},
            "quality_gate": merged.get("quality_gate"),
            "rag_input": normalize_rag_input(merged),
            "judge": {
                k: judge_src.get(k) for k in
                ("provider", "model", "model_version", "temperature",
                 "max_tokens", "timeout", "retry")
            },
            "pipeline": merged.get("pipeline") if isinstance(merged.get("pipeline"), dict) else None,
        }

    @staticmethod
    def _profile_metric(name: str, cfg: Any) -> dict[str, Any]:
        cfg = cfg if isinstance(cfg, dict) else {}
        return {
            "name": name,
            "enabled": bool(cfg.get("enabled", True)),
            "threshold": cfg.get("threshold"),
            "weight": float(cfg.get("weight", 1.0)),
        }

    # ---- metrics ----

    def list_metrics(self) -> list[dict[str, Any]]:
        return [self._metric_dict(spec) for spec in self._registry.list_metrics()]

    @staticmethod
    def _metric_dict(spec) -> dict[str, Any]:
        return {
            "name": spec.name,
            "category": spec.category,
            "engine": spec.engine,
            "version": spec.version,
            "description": spec.description,
            "input_requirements": list(spec.input_requirements),
            "direction": spec.direction,
            "default_severity": spec.default_severity,
        }
