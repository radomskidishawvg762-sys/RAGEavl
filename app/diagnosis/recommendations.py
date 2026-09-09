"""Recommendation templates (T-11 MVP, rule-based only — NO LLM).

Advisory text per Spec FR-26: recommendations NEVER write to any user system.
Priority 1 = highest. source is always "rule" in MVP.
"""

from __future__ import annotations

from app.diagnosis.taxonomy import DiagnosisRule
from app.domain.schemas import Recommendation

# Per-code minimal action templates (user decision 2026-08-29, §十).
_ACTION_TEMPLATES: dict[str, list[tuple[str, int]]] = {
    "integrity.numerical_mismatch": [
        ("检查数值抽取与单位归一化配置是否一致", 1),
        ("在生成侧增加数值一致性约束（要求按参考口径复述数值与单位）", 2),
    ],
    "integrity.temporal_mismatch": [
        ("检查报告期识别与时间标准化配置", 1),
        ("在 Query/Prompt 中显式声明时间范围约束", 2),
    ],
    "integrity.entity_mismatch": [
        ("检查实体消歧配置（alias 表 / metadata canonical）", 1),
        ("检查 Retrieval Context 是否召回了正确实体的相关内容", 2),
    ],
    "integrity.unit_mismatch": [
        ("检查单位归一化与数据源表达口径是否一致", 1),
        ("统一数据源单位口径后重跑评估", 2),
    ],
    # Post-MVP Phase 5: retrieval rules reuse the same template mechanism.
    "retrieval.missing_evidence": [
        ("检查知识库是否包含参考证据所需内容（知识覆盖）", 1),
        ("检查检索配置（top_k / metadata filter / 排序）为何未召回参考证据", 2),
    ],
    "retrieval.top_k_issue": [
        ("评估提高 top_k 使其不低于所需参考证据数量", 1),
        ("检查召回排序，确保有限 top_k 优先命中参考证据", 2),
    ],
    # Post-MVP Phase 6: generation rules reuse the same template mechanism.
    "generation.unsupported_claim": [
        ("将答案约束为仅基于召回上下文作答（grounding / 引用约束）", 1),
        ("检查生成端是否引入上下文之外的参数知识并加以限制", 2),
    ],
    "generation.partial_answer": [
        ("对照参考要点检查答案完整性（要素 / 数值覆盖）", 1),
        ("调整 Prompt 要求覆盖全部参考要点后重跑评估", 2),
    ],
}


def build_recommendations(rule: DiagnosisRule) -> list[Recommendation]:
    actions = _ACTION_TEMPLATES.get(rule.code)
    if not actions:
        return [Recommendation(action=rule.recommendation_template, priority=1, source="rule")]
    return [Recommendation(action=action, priority=priority, source="rule") for action, priority in actions]
