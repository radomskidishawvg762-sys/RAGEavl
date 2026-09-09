"""Metric bootstrap: code-level registration entry point (PRD Q8 — the
metric_definitions TABLE is a mirror only; registration happens here).

Called at app startup; safe when the `eval` extra is absent (engine metrics
simply not registered, and any Run requesting them fails with clear errors).
"""

from __future__ import annotations

import logging

from app.metrics.registry import MetricRegistry, default_registry

logger = logging.getLogger(__name__)


def bootstrap_metrics(registry: MetricRegistry | None = None) -> None:
    reg = registry or default_registry

    # Integrity metrics (T-10) — pure deterministic, no extra deps needed.
    if not reg.has("numerical_consistency"):
        from app.engines.integrity import INTEGRITY_METRIC_VERSION, IntegrityEngine

        IntegrityEngine().register_into(reg)
        logger.info(
            "metrics registered: %s (metric_version=%s)",
            [s.name for s in reg.list_metrics(category="integrity")],
            INTEGRITY_METRIC_VERSION,
        )

    # RAGAS general metrics (T-08) — require the eval extra.
    from app.engines.ragas import METRIC_VERSION, ragas_available

    if not ragas_available():
        logger.warning("ragas not installed (eval extra) — general metrics not registered")
        return
    if reg.has("faithfulness"):
        return  # idempotent

    from app.engines.judge import build_judge, judge_config_from_settings
    from app.engines.ragas import RagasEngine

    engine = RagasEngine(judge=build_judge(judge_config_from_settings()))
    engine.register_into(reg)
    logger.info("metrics registered: %s (metric_version=%s)", engine.metric_names(), METRIC_VERSION)
