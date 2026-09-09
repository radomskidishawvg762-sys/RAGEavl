"""ConfigImportService — Configuration Lifecycle v1 (Phase B).

Formal Import entry (task §5): YAML -> Parse -> Validate -> [Preview] -> Create
Profile Version. Validation ONLY rules; nothing here executes metrics, touches
engines, or mutates evaluation behavior.

Design contracts (Phase A approved D1/D2/D3-A, Freeze invariants 1-10):
  D1  A profile version IS an evaluation_configs row: name="{profile}:v{N}",
      rows are created once and never UPDATEd/DELETEd (invariant 2). History
      runs keep their own config_id (invariant 1).
  D2  Stored rows carry the full imported layer-3 body in profile_config:
        {"profile", "version", "source": "imported", "yaml", "body"}
      Pointer rows (legacy POST /projects/{pid}/configs) keep the thin
      {"profile": n} shape and resolve against the deployment YAML as before.
  D3  Project-scoped import: every entry point requires project_id.
  §8  config_version = sha256(canonical merged effective config) — computed by
      the EXISTING ConfigService.config_version, never redefined here.

Re-importing byte-identical YAML for the latest version is idempotent: the
existing row is returned with created=False (same content = same config row;
invariant 4 keeps bodies stable per config_id).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml

from app.core.errors import ConfigImportInvalidError, NotFoundError
from app.metrics.registry import MetricRegistry, default_registry
from app.repositories.evaluation import EvaluationRepository
from app.repositories.project import ProjectRepository
from app.services.config_resource_service import resolve_config_row
from app.services.config_service import ConfigService, default_config_service

# ---- validation vocabularies (platform-declared shapes, NOT thresholds) ----

_ALLOWED_TOP_LEVEL_KEYS = (
    "profile", "metrics", "severity_mapping", "diagnosis",
    "quality_gate", "pipeline", "rag_input", "alias_table",
)
_ALLOWED_METRIC_KEYS = {"enabled", "threshold", "weight"}
_SEVERITY_LEVELS = {"INFO", "WARNING", "ERROR", "CRITICAL"}
_ALLOWED_DIAGNOSIS_KEYS = {"enabled"}
_ALLOWED_GATE_KEYS = {"enabled", "required_metrics"}
_ALLOWED_PIPELINE_KEYS = {"engines", "diagnosis"}
_RAG_MODES = {"golden_replay", "http"}
_ALLOWED_RAG_KEYS = {"mode", "url", "timeout", "retry"}
_PROFILE_NAME_MAX = 64
_VERSION_PREFIX = "v"

# invariant 7: environment secrets can never enter stored body / DB / export
_SECRET_KEY_NAMES = {"api_key", "apikey", "secret", "token", "password",
                     "authorization", "connection_string"}


def _validation_error(errors: list[dict[str, str]]) -> ConfigImportInvalidError:
    return ConfigImportInvalidError(
        "configuration import validation failed",
        context={"errors": errors},
    )


def _err(errors: list[dict[str, str]], path: str, message: str) -> None:
    errors.append({"path": path, "message": message})


class ConfigImportService:
    def __init__(
        self,
        repo: EvaluationRepository,
        project_repo: ProjectRepository,
        config_service: ConfigService | None = None,
        registry: MetricRegistry | None = None,
    ) -> None:
        self._repo = repo
        self._project_repo = project_repo
        self._cfg = config_service or default_config_service
        self._registry = registry or default_registry

    # ---------------- parse + validate (task §5: 10 checks) ----------------

    def parse(self, yaml_text: str) -> dict:
        """YAML syntax + top-level schema check. Returns the parsed body dict."""
        errors: list[dict[str, str]] = []
        if not isinstance(yaml_text, str) or not yaml_text.strip():
            raise _validation_error([{"path": "yaml", "message": "YAML text is empty"}])
        try:
            body = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            loc = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
            raise _validation_error(
                [{"path": "yaml", "message": f"invalid YAML syntax{loc}: {e.problem or e}"}]
            ) from e
        if not isinstance(body, dict):
            raise _validation_error(
                [{"path": "yaml", "message": "top level must be a YAML mapping"}]
            )
        for key in body:
            if key not in _ALLOWED_TOP_LEVEL_KEYS:
                _err(errors, str(key), f"unknown top-level key (allowed: {', '.join(_ALLOWED_TOP_LEVEL_KEYS)})")
        if errors:
            raise _validation_error(errors)
        return body

    def validate(self, body: dict) -> list[str]:
        """Full semantic validation (§5 checks 3-10). Returns warnings; raises
        ConfigImportInvalidError with structured [{path, message}] on failure."""
        errors: list[dict[str, str]] = []
        warnings: list[str] = []

        self._secret_scan(body, "", errors)
        self._validate_profile(body.get("profile"), errors)
        self._validate_metrics(body.get("metrics"), errors)
        self._validate_severity_mapping(body.get("severity_mapping"), errors)
        self._validate_diagnosis(body.get("diagnosis"), errors)
        self._validate_quality_gate(body.get("quality_gate"), errors)
        self._validate_pipeline(body.get("pipeline"), errors)
        self._validate_rag_input(body.get("rag_input"), errors)
        self._validate_alias_table(body.get("alias_table"), errors)

        # version-field agreement (body stored verbatim — assigned version wins)
        profile_section = body.get("profile")
        if isinstance(profile_section, dict) and "version" in profile_section:
            warnings.append(
                "profile.version in imported YAML is informational only; "
                "the system assigns the stored version (vN)"
            )
        if errors:
            raise _validation_error(errors)
        return warnings

    def _validate_profile(self, profile: Any, errors: list) -> None:
        if profile is None:
            _err(errors, "profile", "profile section is required")
            return
        if not isinstance(profile, dict):
            _err(errors, "profile", "profile must be a mapping")
            return
        name = profile.get("name")
        if not isinstance(name, str) or not name:
            _err(errors, "profile.name", "profile.name is required")
            return
        if len(name) > _PROFILE_NAME_MAX or not all(
            c.isalnum() or c in "-_" for c in name
        ):
            _err(errors, "profile.name",
                 f"profile.name must be 1-{_PROFILE_NAME_MAX} chars [a-zA-Z0-9_-]")

    def _validate_metrics(self, metrics: Any, errors: list) -> None:
        if not isinstance(metrics, dict) or not metrics:
            _err(errors, "metrics", "metrics must be a non-empty mapping")
            return
        registered = {spec.name for spec in self._registry.list_metrics()}
        for name, cfg in metrics.items():
            path = f"metrics.{name}"
            if name not in registered:
                _err(errors, path, f"unknown metric (not registered): {name}")
                continue
            if not isinstance(cfg, dict):
                _err(errors, path, "metric config must be a mapping")
                continue
            for key in cfg:
                if key not in _ALLOWED_METRIC_KEYS:
                    _err(errors, f"{path}.{key}",
                         f"unknown metric field (allowed: {', '.join(sorted(_ALLOWED_METRIC_KEYS))})")
            enabled = cfg.get("enabled", True)
            if not isinstance(enabled, bool):
                _err(errors, f"{path}.enabled", "enabled must be a boolean")
            if "threshold" in cfg and cfg["threshold"] is not None:
                t = cfg["threshold"]
                if not isinstance(t, (int, float)) or isinstance(t, bool):
                    _err(errors, f"{path}.threshold", "threshold must be a number or null")
                elif not 0.0 <= float(t) <= 1.0:
                    _err(errors, f"{path}.threshold",
                         f"threshold must be within [0, 1] (got {t}); null = no PASS/FAIL judgment")
            if "weight" in cfg:
                w = cfg["weight"]
                if not isinstance(w, (int, float)) or isinstance(w, bool):
                    _err(errors, f"{path}.weight", "weight must be a number")
                elif float(w) < 0:
                    _err(errors, f"{path}.weight", f"weight must be >= 0 (got {w})")

    def _validate_severity_mapping(self, mapping: Any, errors: list) -> None:
        if mapping is None:
            return
        if not isinstance(mapping, dict):
            _err(errors, "severity_mapping", "severity_mapping must be a mapping")
            return
        for code, level in mapping.items():
            path = f"severity_mapping.{code}"
            if not isinstance(code, str) or not code.strip():
                _err(errors, path, "severity mapping key must be a non-empty failure code")
                continue
            if level not in _SEVERITY_LEVELS:
                _err(errors, path,
                     f"severity must be one of {sorted(_SEVERITY_LEVELS)} (got {level!r})")

    def _validate_diagnosis(self, diagnosis: Any, errors: list) -> None:
        if diagnosis is None:
            return
        if not isinstance(diagnosis, dict):
            _err(errors, "diagnosis", "diagnosis must be a mapping")
            return
        for key in diagnosis:
            if key not in _ALLOWED_DIAGNOSIS_KEYS:
                _err(errors, f"diagnosis.{key}",
                     f"unknown diagnosis field (allowed: {', '.join(sorted(_ALLOWED_DIAGNOSIS_KEYS))})")
        if "enabled" in diagnosis and not isinstance(diagnosis["enabled"], bool):
            _err(errors, "diagnosis.enabled", "diagnosis.enabled must be a boolean")

    def _validate_quality_gate(self, gate: Any, errors: list) -> None:
        if gate is None:
            return
        if not isinstance(gate, dict) or not gate:
            _err(errors, "quality_gate", "quality_gate must be a mapping or null")
            return
        for key in gate:
            if key not in _ALLOWED_GATE_KEYS:
                _err(errors, f"quality_gate.{key}",
                     f"unknown quality_gate field (allowed: {', '.join(sorted(_ALLOWED_GATE_KEYS))})")
        if "enabled" in gate and not isinstance(gate["enabled"], bool):
            _err(errors, "quality_gate.enabled", "quality_gate.enabled must be a boolean")
        required = gate.get("required_metrics")
        if required is not None:
            if not isinstance(required, list) or not required:
                _err(errors, "quality_gate.required_metrics",
                     "required_metrics must be a non-empty list when present")
                return
            registered = {spec.name for spec in self._registry.list_metrics()}
            for name in required:
                if name not in registered:
                    _err(errors, f"quality_gate.required_metrics.{name}",
                         f"unknown metric (not registered): {name}")

    def _validate_pipeline(self, pipeline: Any, errors: list) -> None:
        if pipeline is None:
            return
        if not isinstance(pipeline, dict):
            _err(errors, "pipeline", "pipeline must be a mapping")
            return
        for key in pipeline:
            if key not in _ALLOWED_PIPELINE_KEYS:
                _err(errors, f"pipeline.{key}",
                     f"unknown pipeline field (allowed: {', '.join(sorted(_ALLOWED_PIPELINE_KEYS))})")
        engines = pipeline.get("engines")
        if engines is not None:
            if not isinstance(engines, list) or not engines:
                _err(errors, "pipeline.engines",
                     "engines must be a non-empty list when present")
            else:
                known = {spec.engine for spec in self._registry.list_metrics()}
                for i, engine in enumerate(engines):
                    if engine not in known:
                        _err(errors, f"pipeline.engines[{i}]",
                             f"unknown engine (registered: {sorted(known)}): {engine}")
        diag = pipeline.get("diagnosis")
        if diag is not None:
            if not isinstance(diag, dict):
                _err(errors, "pipeline.diagnosis", "pipeline.diagnosis must be a mapping")
            elif "enabled" in diag and not isinstance(diag["enabled"], bool):
                _err(errors, "pipeline.diagnosis.enabled",
                     "pipeline.diagnosis.enabled must be a boolean")

    def _validate_rag_input(self, rag: Any, errors: list) -> None:
        if rag is None:
            return
        if not isinstance(rag, dict):
            _err(errors, "rag_input", "rag_input must be a mapping")
            return
        for key in rag:
            if key not in _ALLOWED_RAG_KEYS:
                _err(errors, f"rag_input.{key}",
                     f"unknown rag_input field (allowed: {', '.join(sorted(_ALLOWED_RAG_KEYS))})")
        mode = rag.get("mode", "golden_replay")
        if mode not in _RAG_MODES:
            _err(errors, "rag_input.mode",
                 f"mode must be one of {sorted(_RAG_MODES)} (got {mode!r})")
            return
        url = rag.get("url")
        if mode == "http":
            if not isinstance(url, str) or not url.strip():
                _err(errors, "rag_input.url", "mode=http requires a non-empty url")
        elif url is not None:
            _err(errors, "rag_input.url",
                 "mode=golden_replay must not carry a url (replay and HTTP are separate modes)")
        for key in ("timeout", "retry"):
            if key in rag:
                v = rag[key]
                if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                    _err(errors, f"rag_input.{key}", f"{key} must be a positive integer")

    def _validate_alias_table(self, alias: Any, errors: list) -> None:
        if alias is None:
            return
        if not isinstance(alias, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in alias.items()
        ):
            _err(errors, "alias_table", "alias_table must be a mapping of string to string")

    def _secret_scan(self, node: Any, path: str, errors: list) -> None:
        """invariant 7: no secret-bearing key or environment token may enter the
        stored body (-> DB -> export). Judge secrets live in system.yaml env
        tokens only, never in imported profiles."""
        if isinstance(node, dict):
            for k, v in node.items():
                key_path = f"{path}.{k}" if path else str(k)
                if isinstance(k, str) and k.lower() in _SECRET_KEY_NAMES:
                    _err(errors, key_path,
                         "secret-bearing fields are forbidden in profile configuration; "
                         "secrets come from environment variables only")
                self._secret_scan(v, key_path, errors)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                self._secret_scan(v, f"{path}[{i}]", errors)
        elif isinstance(node, str) and "${" in node:
            _err(errors, path,
                 "environment tokens (${VAR}) are not allowed in profile layer; "
                 "configure environment-dependent values in system.yaml")

    # ---------------- versioning (D1: row = version, immutable) ----------------

    def _stored_versions(self, project_id: str, profile: str) -> list[Any]:
        rows = []
        for row in self._repo.list_configs_for_project(project_id):
            pc = row.profile_config if isinstance(row.profile_config, dict) else {}
            if pc.get("source") == "imported" and pc.get("profile") == profile:
                rows.append(row)
        return rows

    @staticmethod
    def _version_number(row: Any) -> int:
        version = (row.profile_config or {}).get("version", "")
        return int(version[1:]) if str(version).startswith(_VERSION_PREFIX) and str(version)[1:].isdigit() else 0

    def _allocate(self, project_id: str, profile: str) -> tuple[int, str]:
        existing = self._stored_versions(project_id, profile)
        n = max((self._version_number(r) for r in existing), default=0) + 1
        return n, f"{_VERSION_PREFIX}{n}"

    # ---------------- diff (task §19: structured, not text) ----------------

    @staticmethod
    def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
        flat: dict[str, Any] = {}
        if isinstance(node, dict):
            for k, v in node.items():
                flat.update(ConfigImportService._flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        else:
            flat[prefix] = node
        return flat

    def diff(self, old_body: dict | None, new_body: dict) -> list[dict[str, Any]]:
        """Structured Added/Removed/Changed diff over flattened dotted paths."""
        old = self._flatten(old_body) if old_body is not None else {}
        new = self._flatten(new_body)
        items: list[dict[str, Any]] = []
        for path in sorted(set(old) | set(new)):
            if path not in old:
                items.append({"path": path, "change": "added", "old": None, "new": new[path]})
            elif path not in new:
                items.append({"path": path, "change": "removed", "old": old[path], "new": None})
            elif old[path] != new[path]:
                items.append({"path": path, "change": "changed", "old": old[path], "new": new[path]})
        return items

    def _base_body(self, project_id: str, profile: str) -> tuple[dict | None, str | None]:
        """Diff base: latest stored version of the same profile; else the
        deployment YAML profile with the same name (§24 migration path
        default.yaml -> default:v1); else None (everything is 'added')."""
        stored = self._stored_versions(project_id, profile)
        if stored:
            latest = max(stored, key=self._version_number)
            pc = latest.profile_config or {}
            return deepcopy(pc.get("body")), pc.get("version")
        raw = self._cfg.raw_profile_yaml(profile)
        if raw is not None:
            try:
                return yaml.safe_load(raw), None
            except yaml.YAMLError:
                return None, None
        return None, None

    # ---------------- preview + import (§6: Import -> Validate -> Preview) ----

    def preview(self, *, project_id: str, domain: str, yaml_text: str) -> dict[str, Any]:
        """Validate + build the full preview payload. NO persistence — the
        caller explicitly creates the version afterwards (§6)."""
        self._require_project(project_id)
        self._require_domain(domain)
        body = self.parse(yaml_text)
        warnings = self.validate(body)
        next_n, next_version = self._allocate(project_id, self._profile_name(body))
        base_body, base_version = self._base_body(project_id, self._profile_name(body))
        payload = self._payload(
            domain=domain,
            body=body,
            version=next_version,
            warnings=warnings,
            diff_items=self.diff(base_body, body),
            diff_base=base_version if base_version else ("yaml" if base_body is not None else None),
        )
        payload["project_id"] = project_id
        return payload

    def import_version(
        self, *, project_id: str, domain: str, yaml_text: str
    ) -> tuple[Any, bool, dict[str, Any]]:
        """Validate + create a NEW immutable profile version row (or return the
        latest row unchanged when the body is identical — idempotent re-import).

        Returns (row, created, payload). Raises ConfigImportInvalidError (422)
        with structured field errors on any validation failure."""
        self._require_project(project_id)
        self._require_domain(domain)
        body = self.parse(yaml_text)
        warnings = self.validate(body)
        profile = self._profile_name(body)

        stored = self._stored_versions(project_id, profile)
        if stored:
            latest = max(stored, key=self._version_number)
            if (latest.profile_config or {}).get("body") == body:
                base_version = (latest.profile_config or {}).get("version")
                payload = self._payload(
                    domain=domain, body=body, version=base_version,
                    warnings=warnings + ["identical to latest version; existing row returned"],
                    diff_items=[], diff_base=base_version,
                )
                payload["project_id"] = project_id
                return latest, False, payload

        n, version = self._allocate(project_id, profile)
        # diff base must be captured BEFORE the new row exists (otherwise v1
        # diffs against itself) — latest stored version or same-named YAML file
        base_body, base_version = self._base_body(project_id, profile)
        config_version = self._cfg.config_version(domain, profile, profile_body=body)
        name = f"{profile}:{version}"
        if self._repo.get_config_by_name(project_id, name) is not None:  # uq guard
            raise _validation_error(
                [{"path": "profile", "message": f"config name '{name}' already exists"}]
            )
        row = self._repo.create_config(
            project_id=project_id,
            name=name,
            domain_config={"domain": domain},
            profile_config={
                "profile": profile,
                "version": version,
                "source": "imported",
                "yaml": yaml_text,
                "body": deepcopy(body),  # invariant 6: stored == imported
            },
            pipeline_config=body.get("pipeline") if isinstance(body.get("pipeline"), dict) else None,
            config_version=config_version,
        )
        payload = self._payload(
            domain=domain, body=body, version=version, warnings=warnings,
            diff_items=self.diff(base_body, body) if base_body is not None else [],
            diff_base=base_version if base_version else ("yaml" if base_body is not None else None),
        )
        payload["project_id"] = project_id
        payload["config_id"] = row.id
        return row, True, payload

    def list_configs(self, project_id: str) -> list[Any]:
        """All config rows of a project (pointer + stored versions), stable
        creation order. Read-only surface for the Profiles workspace (§10)."""
        self._require_project(project_id)  # 404 when unknown
        return self._repo.list_configs_for_project(project_id)

    def config_detail(self, config_id: str) -> dict[str, Any]:
        """Profile Detail payload (§11) for BOTH row shapes: Overview/Metrics/
        Severity/Pipeline/Judge/RAG/Quality Gate/Raw YAML. Stored rows resolve
        exclusively from the immutable persisted body (invariant 4); pointer
        rows from the deployment YAML file. config_version always reports the
        row's STORED value — the historical fact, never recomputed."""
        cfg = self._repo.get_config(config_id)
        domain, profile, body = resolve_config_row(cfg)
        pc = cfg.profile_config if isinstance(cfg.profile_config, dict) else {}
        if body is None:
            body = self._cfg.profile_body(profile)
            if body is None:
                raise NotFoundError(
                    f"profile '{profile}' has no stored body or deployment YAML"
                )
        payload = self._payload(
            domain=domain, body=body, version=pc.get("version"),
            warnings=[], diff_items=[], diff_base=None,
        )
        payload["config_version"] = cfg.config_version
        payload.update({
            "config_id": cfg.id,
            "name": cfg.name,
            "source": "imported" if pc.get("source") == "imported" else "yaml_pointer",
            "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
            "yaml": pc.get("yaml") or self._cfg.raw_profile_yaml(profile),
        })
        return payload

    def export_yaml(self, config_id: str) -> dict[str, Any]:
        """Export MUST be based on the stored configuration (task §18) — the
        persisted raw YAML for imported rows, the deployment YAML file content
        for legacy pointer rows. Never reassembled by the frontend."""
        cfg = self._repo.get_config(config_id)  # 404 when missing
        domain, profile, body = resolve_config_row(cfg)
        pc = cfg.profile_config if isinstance(cfg.profile_config, dict) else {}
        if body is not None:
            raw = pc.get("yaml")
            version = pc.get("version")
        else:
            raw = self._cfg.raw_profile_yaml(profile)
            version = (cfg.profile_config or {}).get("version")
            if raw is None:
                raise NotFoundError(f"profile '{profile}' has no stored or file-backed YAML")
        return {
            "config_id": cfg.id,
            "profile": profile,
            "version": version,
            "domain": domain,
            "config_version": cfg.config_version,
            "yaml": raw,
        }

    # ---------------- helpers ----------------

    @staticmethod
    def _profile_name(body: dict) -> str:
        profile = body.get("profile")
        return str(profile.get("name")) if isinstance(profile, dict) and profile.get("name") else "unnamed"

    def _require_project(self, project_id: str) -> None:
        if self._project_repo.get(project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    def _require_domain(self, domain: str) -> None:
        if domain not in self._cfg.domain_names():
            raise ConfigImportInvalidError(
                f"unknown domain: {domain}",
                context={"errors": [{"path": "domain", "message": f"unknown domain: {domain}"}]},
            )

    def _payload(
        self, *, domain: str, body: dict, version: str | None,
        warnings: list[str], diff_items: list[dict], diff_base: str | None,
    ) -> dict[str, Any]:
        """Preview/commit payload (§6): profile identity, metrics, severity,
        quality gate, pipeline, judge (public fields), rag input, diff."""
        profile = self._profile_name(body)
        config_version = self._cfg.config_version(domain, profile, profile_body=body)
        merged = self._cfg.load(domain, profile, profile_body=body)

        metrics_out = []
        for name, cfg in sorted((body.get("metrics") or {}).items()):
            cfg = cfg if isinstance(cfg, dict) else {}
            spec = self._registry.get_metric(name).spec
            metrics_out.append({
                "name": name,
                "enabled": bool(cfg.get("enabled", True)),
                "threshold": cfg.get("threshold"),
                "weight": float(cfg.get("weight", 1.0)),
                "direction": spec.direction,
            })

        system = merged.get("system") if isinstance(merged.get("system"), dict) else {}
        judge_src = system.get("judge") if isinstance(system.get("judge"), dict) else {}
        judge = {k: judge_src.get(k) for k in
                 ("provider", "model", "model_version", "temperature", "max_tokens", "timeout", "retry")}
        rag = body.get("rag_input") if isinstance(body.get("rag_input"), dict) else None
        if rag is None:
            sys_rag = system.get("rag_input")
            rag = {k: sys_rag.get(k) for k in ("url", "timeout", "retry")} if isinstance(sys_rag, dict) else None
        rag = rag or {"mode": "golden_replay", "url": None, "timeout": None, "retry": None}

        return {
            "profile": profile,
            "version": version,
            "domain": domain,
            "config_version": config_version,
            "metrics": metrics_out,
            "severity_mapping": dict(body.get("severity_mapping") or {}),
            "quality_gate": body.get("quality_gate"),
            "pipeline": body.get("pipeline") or {"engines": None, "diagnosis": body.get("diagnosis")},
            "judge": judge,
            "rag_input": rag,
            "warnings": warnings,
            "diff": {"base": diff_base, "items": diff_items},
        }
