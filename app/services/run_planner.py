"""Run planning (T-13): Profile -> execution parameters, no Router leakage.

Profile (merged config) -> RunPlan:
  enabled_metrics      metrics whose `enabled` is true
  params               EvalParams carrying severity_mapping / per-metric
                       threshold / alias_table — the API layer never hardcodes
                       thresholds or severities (Spec Appendix B, PRD Q3).
  config_version       sha256 snapshot (ConfigService.config_version)

Reproducibility meta fills the 8 mandatory fields (Spec 7.1) from what the
merged config + registry can provide; unset pieces stay None rather than
guessing.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.core.errors import ConfigInvalidError
from app.engines.base import EvalParams
from app.metrics.registry import default_registry

_REPRO_FIELDS = (
    "dataset_version", "config_version", "metric_version", "prompt_version",
    "judge_model", "judge_model_version", "model_version", "timestamp",
)


class RunPlan(BaseModel):
    enabled_metrics: list[str]
    params: EvalParams = Field(default_factory=EvalParams)


class MetricOverride(BaseModel):
    """Run-level metric override (Spec 6.3 `metric_overrides`).

    Presence semantics (pydantic `model_fields_set`):
      field absent        -> keep the Profile value
      threshold: null     -> clear PASS/FAIL judgment for this run
      threshold: <number> -> set the run threshold
      enabled / weight    -> must be a real value when present
                              (explicit null enabled/weight is invalid)
    Weight must be >= 0 (overall score weights it)."""

    enabled: bool | None = None
    threshold: float | None = None
    weight: float | None = None


def apply_metric_overrides(
    merged: dict, overrides: dict[str, MetricOverride]
) -> tuple[dict, dict]:
    """Merge Run-level overrides into a deep COPY of the merged config.

    Returns (effective_merged, applied) where `applied` echoes only the fields
    actually set. The original `merged` is never mutated. config_version keeps
    its sha256(merged YAML) semantics (ADR-05/G6) — overrides live ONLY in the
    run snapshot (reproducibility_meta), never back into YAML or DB config.

    Raises ConfigInvalidError (BIZ_CONFIG_INVALID) on:
      unknown metric name | explicit-null enabled/weight | negative weight."""
    if not overrides:
        return merged, {}
    metrics = merged.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ConfigInvalidError(
            "evaluation profile has no metrics configuration",
            code="BIZ_CONFIG_INVALID",
        )
    unknown = sorted(set(overrides) - set(metrics))
    if unknown:
        raise ConfigInvalidError(
            f"metric_overrides reference unknown metrics: {unknown}",
            code="BIZ_CONFIG_INVALID",
        )
    effective = deepcopy(merged)
    eff_metrics = dict(effective["metrics"])
    applied: dict[str, dict] = {}
    for name, override in overrides.items():
        fields = set(override.model_fields_set) & {"enabled", "threshold", "weight"}
        if not fields:
            continue
        entry = dict(eff_metrics.get(name) or {})
        for field in ("enabled", "threshold", "weight"):
            if field not in fields:
                continue
            value = getattr(override, field)
            if field in ("enabled", "weight") and value is None:
                raise ConfigInvalidError(
                    f"metric_overrides[{name}].{field} must be a concrete value, not null",
                    code="BIZ_CONFIG_INVALID",
                )
            if field == "weight" and value < 0:
                raise ConfigInvalidError(
                    f"metric_overrides[{name}].weight must be >= 0",
                    code="BIZ_CONFIG_INVALID",
                )
            entry[field] = value
            applied.setdefault(name, {})[field] = value
        eff_metrics[name] = entry
    effective["metrics"] = eff_metrics
    return effective, applied


def effective_metrics_snapshot(merged: dict, enabled_metrics: list[str]) -> dict:
    """Per-metric RUN-LEVEL effective config (threshold/weight) for the enabled
    set — the "actually executed parameters" trace in reproducibility_meta."""
    metrics = merged.get("metrics") if isinstance(merged.get("metrics"), dict) else {}
    snapshot: dict[str, dict] = {}
    for name in enabled_metrics:
        cfg = metrics.get(name) if isinstance(metrics.get(name), dict) else {}
        snapshot[name] = {
            "threshold": cfg.get("threshold"),
            "weight": float(cfg.get("weight", 1.0)),
        }
    return snapshot


def normalize_rag_input(merged: dict) -> dict:
    """Effective RAG input (Phase D, task §6): profile-layer top-level
    `rag_input` overrides the system layer; mode is inferred (url -> http)
    when absent. Returns {mode, url, timeout, retry} — configuration only,
    never secrets (invariant 7)."""
    profile_rag = merged.get("rag_input") if isinstance(merged.get("rag_input"), dict) else None
    system = merged.get("system") if isinstance(merged.get("system"), dict) else {}
    sys_rag = system.get("rag_input") if isinstance(system.get("rag_input"), dict) else {}
    src = profile_rag if profile_rag is not None else sys_rag

    # An explicit profile-layer `rag_input` is authoritative and is never
    # overridden. Only the SYSTEM layer (which is what ships: system.yaml carries
    # `url: ${RAG_INPUT_URL:}`) falls back to the process Settings — the same
    # .env.local / .env.test every other setting comes from.
    settings_fallback = _settings() if profile_rag is None else None

    url = str(src.get("url") or "").strip() or None
    if url is None and settings_fallback is not None:
        url = (settings_fallback.rag_input_url or "").strip() or None
    mode = src.get("mode") or ("http" if url else "golden_replay")
    if mode not in ("golden_replay", "http"):  # tolerate unknown explicit mode
        mode = "http" if url else "golden_replay"
    timeout = src.get("timeout")
    retry = src.get("retry")
    if settings_fallback is not None:
        # Only fill gaps — an absent timeout/retry must not become None where
        # system.yaml (or the adapter) supplies its own default.
        if timeout is None:
            timeout = settings_fallback.rag_input_timeout
        if retry is None:
            retry = settings_fallback.rag_input_retry
    return {"mode": mode, "url": url, "timeout": timeout, "retry": retry}


def _settings():
    """Local import: keeps run_planner importable without a configured env."""
    from app.core.config import settings

    return settings


def resolve_effective_pipeline(merged: dict, enabled_metrics: list[str]) -> dict:
    """Effective pipeline (Phase D, task §4/§5): pipeline.engines whitelist
    applied to the enabled metric set, plus diagnosis.enabled.

    Returns {engines, selected_engines, excluded_metrics, diagnosis}:
      engines           ACTUAL engine set the factory will build (sorted) —
                        invariant: snapshot.pipeline.engines == created engines
      selected_engines  the raw whitelist (None = derive from metrics)
      excluded_metrics  enabled metrics dropped by the whitelist, {name: engine}
                        — explicit selection, NEVER silent
      unknown_metrics   enabled metrics absent from the registry (engine backend
                        not installed); resolve_profile turns these into a 409
      diagnosis         {"enabled": bool} — pipeline.diagnosis.enabled wins over
                        the legacy top-level diagnosis.enabled; default True
    """
    pipeline = merged.get("pipeline") if isinstance(merged.get("pipeline"), dict) else {}
    selected_raw = pipeline.get("engines")
    selected = [str(e) for e in selected_raw] if isinstance(selected_raw, list) and selected_raw else None
    pipe_diag = pipeline.get("diagnosis") if isinstance(pipeline.get("diagnosis"), dict) else {}
    legacy_diag = merged.get("diagnosis") if isinstance(merged.get("diagnosis"), dict) else {}
    diag_enabled = pipe_diag.get("enabled", legacy_diag.get("enabled", True))

    actual: list[str] = []
    excluded: dict[str, str] = {}
    unknown: list[str] = []
    for name in enabled_metrics:
        try:
            engine = default_registry.get_metric(name).spec.engine
        except KeyError:
            # NOT silently skipped. An enabled metric with no registry entry (its
            # engine backend is not installed) produces zero rows, yet the run
            # used to finish "completed" with a plausible overall_score while the
            # snapshot claimed every configured metric. Collected here and raised
            # by resolve_profile, which runs BEFORE any run row is created and
            # before the dataset row lock is taken (ADR-06: that lock is never
            # released, so failing later would consume the dataset version).
            unknown.append(name)
            continue
        if selected is not None and engine not in selected:
            excluded[name] = engine
            continue
        if engine not in actual:
            actual.append(engine)
    return {
        "engines": sorted(actual),
        "selected_engines": selected,
        "excluded_metrics": excluded,
        "unknown_metrics": unknown,
        "diagnosis": {"enabled": bool(diag_enabled)},
    }


def resolve_profile(merged: dict) -> RunPlan:
    """Merged config -> {enabled metrics, EvalParams}. Raises ConfigInvalidError
    when the profile carries no usable metric configuration."""
    metrics = merged.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ConfigInvalidError(
            "evaluation profile has no metrics configuration",
            code="BIZ_CONFIG_INVALID",
        )

    enabled: list[str] = []
    extra: dict = {}
    for name, cfg in metrics.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            continue
        enabled.append(name)
        per_metric: dict = {}
        if "threshold" in cfg:
            per_metric["threshold"] = cfg.get("threshold")
        if "weight" in cfg:
            per_metric["weight"] = cfg.get("weight", 1.0)
        if per_metric:
            extra[name] = per_metric

    alias_table = merged.get("alias_table") if isinstance(merged.get("alias_table"), dict) else {}
    if alias_table:
        extra["alias_table"] = alias_table

    severity = merged.get("severity_mapping")
    # T-14A: judge config (PUBLIC fields only — api_key never leaves the env ->
    # SecretStr -> adapter chain). The launcher re-attaches the key from Settings.
    from app.services.judge_settings_service import default_judge_settings_service

    judge = default_judge_settings_service.config()
    merged_judge = merged.get("system", {}).get("judge", {}) if isinstance(merged.get("system"), dict) else {}
    if not default_judge_settings_service.has_runtime_override() and isinstance(merged_judge, dict):
        judge = judge.model_copy(update={
            key: value for key, value in merged_judge.items()
            if key in {"provider", "model", "model_version", "base_url", "temperature", "max_tokens", "timeout", "retry"}
            and value not in (None, "")
        })
    if judge.provider or judge.model:
        extra["judge_config"] = judge.public_dump()
    # T-14B: RAG input adapter config — Phase D normalized: profile-layer
    # rag_input overrides system; {mode, url, timeout, retry}, no secrets.
    extra["rag_input"] = normalize_rag_input(merged)
    # Phase D (task §4/§5): effective pipeline — engine whitelist applied to the
    # enabled set (excluded metrics are EXPLICIT, recorded in extra), and the
    # diagnosis on/off switch the service orchestration layer honors.
    pipeline_cfg = resolve_effective_pipeline(merged, enabled)
    if pipeline_cfg["unknown_metrics"]:
        raise ConfigInvalidError(
            "enabled metrics have no engine backend (not registered): "
            f"{', '.join(pipeline_cfg['unknown_metrics'])} — disable them in the "
            "profile or install the extra that provides their engine",
            code="BIZ_CONFIG_INVALID",
            context={
                "metrics": pipeline_cfg["unknown_metrics"],
                "reason": "not_registered",
            },
        )
    extra["pipeline"] = {
        "selected_engines": pipeline_cfg["selected_engines"],
        "excluded_metrics": pipeline_cfg["excluded_metrics"],
        "diagnosis_enabled": pipeline_cfg["diagnosis"]["enabled"],
    }
    enabled = [name for name in enabled if name not in pipeline_cfg["excluded_metrics"]]
    if not enabled:
        # Nothing left to evaluate. Without this guard the run was CREATED: the
        # factory built no engines, every record produced no metric rows, the
        # service counted each record as an error, and — because error_details is
        # only appended on the exception branch — the run finished `failed` with
        # error_summary = NULL, so there was no cause to read anywhere. ADR-06 had
        # already locked the dataset by then, and that lock is never released.
        # Rejecting at plan time keeps the dataset usable.
        raise ConfigInvalidError(
            "no metrics are enabled after applying the profile and any overrides "
            "— a run with nothing to evaluate cannot produce a result",
            code="BIZ_CONFIG_INVALID",
            context={
                "enabled_metrics": [],
                "excluded_metrics": sorted(pipeline_cfg["excluded_metrics"]),
            },
        )
    return RunPlan(
        enabled_metrics=enabled,
        params=EvalParams(
            severity_mapping={str(k): str(v) for k, v in severity.items()}
            if isinstance(severity, dict) else {},
            extra=extra,
        ),
    )


def build_reproducibility_meta(
    *,
    config_version: str,
    dataset_version: str,
    merged: dict,
    enabled_metrics: list[str],
    metric_overrides: dict | None = None,
    profile: str | None = None,
    profile_version: str | None = None,
    pipeline_extra: dict | None = None,
) -> dict:
    """8 mandatory reproducibility fields (Spec 7.1). judge/model pieces come
    from merged config (system.judge) — never hardcoded.

    Phase B (Configuration Lifecycle v1): `profile`/`profile_version` identify
    the exact profile version behind config_version (stored-imported rows carry
    the system-assigned vN; YAML pointers the in-file version) — invariant 6:
    Run Snapshot records profile + profile_version + config_version.

    `merged` must be the EFFECTIVE config (overrides already applied) so
    metric_weights reflects what actually executes; `metric_overrides` is the
    applied override echo. Together with `effective_metrics` this keeps the
    invariant: executed parameters == metric_results == run snapshot."""
    versions: list[str] = []
    for name in enabled_metrics:
        try:
            spec = default_registry.get_metric(name).spec
            if spec.version:
                versions.append(spec.version)
        except KeyError:
            continue
    metric_version = ",".join(dict.fromkeys(versions)) or "unknown"
    judge = merged.get("system", {}).get("judge", {}) if isinstance(merged.get("system"), dict) else {}
    metrics_section = merged.get("metrics") if isinstance(merged.get("metrics"), dict) else {}
    # Phase D (task §3/§4/§6/§8): the snapshot carries the effective pipeline,
    # rag_input and quality_gate so Report/Gate/history NEVER re-read current
    # YAML. quality_gate uses KEY PRESENCE semantics (explicit null = unconfigured).
    # pipeline_extra comes from RunPlan (pre-filter info: whitelist + exclusions);
    # `engines` is ALWAYS recomputed from the effective enabled list so it equals
    # what the factory will actually build.
    if pipeline_extra is not None:
        pipeline_section = {
            "engines": resolve_effective_pipeline(merged, enabled_metrics)["engines"],
            "selected_engines": pipeline_extra.get("selected_engines"),
            "excluded_metrics": dict(pipeline_extra.get("excluded_metrics") or {}),
            "diagnosis": {"enabled": bool(pipeline_extra.get("diagnosis_enabled", True))},
        }
    else:
        pipeline_section = resolve_effective_pipeline(merged, enabled_metrics)
    meta = {
        "dataset_version": dataset_version,
        "config_version": config_version,
        "profile": profile,
        "profile_version": profile_version,
        "metric_version": metric_version,
        "prompt_version": judge.get("prompt_version") if isinstance(judge, dict) else None,
        "judge_model": judge.get("model") if isinstance(judge, dict) else None,
        "judge_model_version": judge.get("model_version") if isinstance(judge, dict) else None,
        "model_version": None,
        "timestamp": datetime.now(UTC).isoformat(),
        # T-14C: enabled metrics + per-metric weight are snapshotted so the
        # Report never has to re-read/re-resolve the Profile (runs replayable)
        "enabled_metrics": list(enabled_metrics),
        "metric_weights": {
            name: float((metrics_section.get(name) or {}).get("weight", 1.0))
            for name in enabled_metrics
        },
        # Phase 1B G4: run-level effective per-metric config + applied override
        # echo — the snapshot IS the executed parameter set, auditable per run
        "effective_metrics": effective_metrics_snapshot(merged, enabled_metrics),
        "metric_overrides": dict(metric_overrides or {}),
        # Phase D: effective pipeline == actual engines (factory derives from the
        # same enabled_metrics + registry), diagnosis switch, RAG input, gate
        "pipeline": {
            "engines": pipeline_section["engines"],
            "selected_engines": pipeline_section["selected_engines"],
            "excluded_metrics": pipeline_section["excluded_metrics"],
            "diagnosis": dict(pipeline_section["diagnosis"]),
        },
        "rag_input": normalize_rag_input(merged),
        "quality_gate": merged.get("quality_gate"),
    }
    # T-14A §七: full judge fingerprint WITHOUT secrets (api_key never recorded)
    if isinstance(judge, dict):
        for key in ("provider", "temperature", "max_tokens", "timeout", "retry"):
            meta[f"judge_{key}"] = judge.get(key)
    return meta


def validate_reproducibility_meta(meta: dict) -> None:
    missing = [f for f in _REPRO_FIELDS if f not in meta]
    if missing:
        raise ConfigInvalidError(
            f"reproducibility_meta missing fields: {missing}", code="BIZ_CONFIG_INVALID"
        )
