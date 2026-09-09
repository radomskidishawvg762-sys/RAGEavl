from __future__ import annotations

import hashlib
import os
import re
from copy import deepcopy
from pathlib import Path

import yaml

_TOKEN_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::([^}]*))?\}")
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _resolve_tokens(obj: object) -> object:
    """Replace ${VAR} / ${VAR:default} from os.environ for runtime use.

    Secrets resolved here live only in-memory and are never logged (RedactingFilter).
    """
    if isinstance(obj, dict):
        return {k: _resolve_tokens(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_tokens(v) for v in obj]
    if isinstance(obj, str):

        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            return default if default is not None else m.group(0)

        return _TOKEN_RE.sub(repl, obj)
    return obj


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = deepcopy(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = deepcopy(v)
    return result


class ConfigService:
    """Three-layer YAML config (ADR-05): system < domain < evaluation profile.

    Priority (later overrides earlier):
      system.yaml < domains/<domain>.yaml < evaluations/<profile>.yaml
    No DB config tables, no hot reload. config_version = sha256 of the *token*
    (pre-resolution) merged form — environment-independent, never embeds secrets.
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or CONFIG_DIR

    def _load_yaml(self, rel: str) -> dict:
        path = self.config_dir / rel
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}

    def _merge(self, domain: str, profile: str, profile_body: dict | None = None) -> dict:
        """Token-form merge. `profile_body` (stored/imported layer-3 dict, Phase B)
        replaces the evaluations/<profile>.yaml file layer when provided — the
        layer order and deep-merge semantics are IDENTICAL either way."""
        system = self._load_yaml("system.yaml")
        dom = self._load_yaml(f"domains/{domain}.yaml")
        prof = profile_body if profile_body is not None else self._load_yaml(f"evaluations/{profile}.yaml")
        return _deep_merge(system, _deep_merge(dom, prof))

    def load(self, domain: str = "general", profile: str = "default",
             profile_body: dict | None = None) -> dict:
        """Resolved merged config for runtime use (tokens expanded).

        `profile_body` is the stored-imported layer-3 content (Configuration
        Lifecycle v1 Phase B). None = legacy YAML-file pointer behavior, bit-for-bit
        unchanged."""
        return _resolve_tokens(self._merge(domain, profile, profile_body))  # type: ignore[return-value]

    def config_version(self, domain: str = "general", profile: str = "default",
                       profile_body: dict | None = None) -> str:
        """sha256 of the token (pre-resolution) merged form.

        Same canonical-dump semantics for YAML profiles and stored bodies alike
        (invariant 5): the hash always covers the full merged effective config
        (system < domain < layer-3), never the raw body alone."""
        merged = self._merge(domain, profile, profile_body)
        dumped = yaml.safe_dump(merged, sort_keys=True, allow_unicode=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    def severity_mapping(self, domain: str = "general", profile: str = "default") -> dict[str, str]:
        """Profile severity_mapping from the merged config (Pre-T13 C3).

        Top-level key per Spec Appendix B:
          severity_mapping:
            numerical_mismatch: CRITICAL   # platform default suggestion, overridable
        Returns {} when unset — callers then fall back to the rule severity_hint.
        """
        merged = self.load(domain, profile)
        mapping = merged.get("severity_mapping", {})
        return {str(k): str(v) for k, v in mapping.items()} if isinstance(mapping, dict) else {}

    def raw_profile_yaml(self, profile: str) -> str | None:
        """Raw layer-3 YAML text for a deployment profile (Phase B export path
        for legacy pointer rows). None when no such file exists."""
        path = self.config_dir / "evaluations" / f"{profile}.yaml"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def profile_body(self, profile: str) -> dict | None:
        """Parsed layer-3 YAML dict for a deployment profile (detail/export path
        for legacy pointer rows). None when no such file exists."""
        data = self._load_yaml(f"evaluations/{profile}.yaml")
        return data or None

    def profile_names(self) -> list[str]:
        """Enumerate available evaluation profiles (config/evaluations/*.yaml), sorted.

        Distinct config source check (T-18): the frontend catalog reads the SAME
        layer-3 profile files the run planner uses — no second profile source.
        """
        profiles_dir = self.config_dir / "evaluations"
        if not profiles_dir.is_dir():
            return []
        return sorted(p.stem for p in profiles_dir.glob("*.yaml"))

    def profile_exists(self, name: str) -> bool:
        """True when an evaluation profile file exists (layer <name>.yaml)."""
        return (self.config_dir / "evaluations" / f"{name}.yaml").is_file()

    def domain_names(self) -> list[str]:
        """Enumerate available domains (config/domains/*.yaml), sorted.

        Source of truth for project-domain validation (FR-01): a project may
        only reference a domain that has a layer-2 YAML, otherwise run creation
        would silently merge system-only config. Falls back to ["general"] when
        the layer is absent so the model default stays valid."""
        domains_dir = self.config_dir / "domains"
        if not domains_dir.is_dir():
            return ["general"]
        names = sorted(p.stem for p in domains_dir.glob("*.yaml"))
        return names or ["general"]


default_config_service = ConfigService()
