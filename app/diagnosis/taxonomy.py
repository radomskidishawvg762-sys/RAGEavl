"""Failure Taxonomy (Spec A.5.1) + DiagnosisRule schema (A.5.2).

14 codes — retrieval.* (7) / generation.* (3) / integrity.* (4).
Implemented rules: the 4 integrity codes (T-11) + 2 retrieval codes
(Post-MVP Phase 5) + 2 generation codes (Post-MVP Phase 6):
  retrieval.missing_evidence / retrieval.top_k_issue
  generation.unsupported_claim / generation.partial_answer
BLOCKED (no evidence source exists — never fabricated):
  retrieval.metadata_filter_issue / retrieval.knowledge_coverage /
  generation.irrelevant_answer.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.schemas import ComparisonType, Severity

RETRIEVAL_CODES = [
    "retrieval.missing_evidence",
    "retrieval.ranking_issue",
    "retrieval.top_k_issue",
    "retrieval.metadata_filter_issue",
    "retrieval.knowledge_coverage",
    "retrieval.chunking_issue",
    "retrieval.query_rewrite_issue",
]
GENERATION_CODES = [
    "generation.unsupported_claim",
    "generation.partial_answer",
    "generation.irrelevant_answer",
]
INTEGRITY_CODES = [
    "integrity.numerical_mismatch",
    "integrity.temporal_mismatch",
    "integrity.entity_mismatch",
    "integrity.unit_mismatch",
]
FAILURE_TAXONOMY: list[str] = RETRIEVAL_CODES + GENERATION_CODES + INTEGRITY_CODES

EVIDENCE_CONTRACT_VERSION = "v1"


class DiagnosisRule(BaseModel):
    code: str  # e.g. integrity.numerical_mismatch
    trigger_metrics: list[str]
    trigger_condition: str  # e.g. "comparison_type == value_mismatch"
    evidence_contract: str  # "<code>.v1"
    severity_hint: Severity  # platform default suggestion, overridable by Profile
    recommendation_template: str


# Spec A.5.2: comparison_type -> taxonomy code. scale_mismatch is a numerical
# mismatch variant (same written value, different magnitude unit).
COMPARISON_TO_RULE: dict[ComparisonType, str] = {
    "value_mismatch": "integrity.numerical_mismatch",
    "scale_mismatch": "integrity.numerical_mismatch",
    "unit_mismatch": "integrity.unit_mismatch",
    "temporal_mismatch": "integrity.temporal_mismatch",
    "entity_mismatch": "integrity.entity_mismatch",
}

# Spec Appendix B severity_mapping example values — platform DEFAULT SUGGESTIONS
# ("平台默认建议，可被 Profile 覆盖"), not hard business thresholds (PRD 附录 B).
DEFAULT_SEVERITY_HINTS: dict[str, Severity] = {
    "integrity.numerical_mismatch": "CRITICAL",
    "integrity.temporal_mismatch": "ERROR",
    "integrity.entity_mismatch": "ERROR",
    "integrity.unit_mismatch": "ERROR",
    "retrieval.missing_evidence": "ERROR",
    "retrieval.top_k_issue": "WARNING",
    "generation.unsupported_claim": "CRITICAL",
    "generation.partial_answer": "WARNING",
}

INTEGRITY_RULES: dict[str, DiagnosisRule] = {
    "integrity.numerical_mismatch": DiagnosisRule(
        code="integrity.numerical_mismatch",
        trigger_metrics=["numerical_consistency"],
        trigger_condition="comparison_type in (value_mismatch, scale_mismatch)",
        evidence_contract="integrity.numerical_mismatch.v1",
        severity_hint=DEFAULT_SEVERITY_HINTS["integrity.numerical_mismatch"],
        recommendation_template="数值不一致：检查数值抽取、单位归一化与生成约束",
    ),
    "integrity.temporal_mismatch": DiagnosisRule(
        code="integrity.temporal_mismatch",
        trigger_metrics=["temporal_consistency"],
        trigger_condition="comparison_type == temporal_mismatch",
        evidence_contract="integrity.temporal_mismatch.v1",
        severity_hint=DEFAULT_SEVERITY_HINTS["integrity.temporal_mismatch"],
        recommendation_template="时间不一致：检查报告期识别与 Query/Prompt 时间约束",
    ),
    "integrity.entity_mismatch": DiagnosisRule(
        code="integrity.entity_mismatch",
        trigger_metrics=["entity_consistency"],
        trigger_condition="comparison_type == entity_mismatch",
        evidence_contract="integrity.entity_mismatch.v1",
        severity_hint=DEFAULT_SEVERITY_HINTS["integrity.entity_mismatch"],
        recommendation_template="实体不一致：检查实体消歧、Metadata 与 Retrieval Context",
    ),
    "integrity.unit_mismatch": DiagnosisRule(
        code="integrity.unit_mismatch",
        trigger_metrics=["numerical_consistency"],
        trigger_condition="comparison_type == unit_mismatch",
        evidence_contract="integrity.unit_mismatch.v1",
        severity_hint=DEFAULT_SEVERITY_HINTS["integrity.unit_mismatch"],
        recommendation_template="单位不一致：检查单位归一化与数据源表达口径",
    ),
}


# Post-MVP Phase 5 (evidence-first retrieval diagnosis): a threshold failure
# on a retrieval metric only TRIGGERS the check — the code is attributed ONLY
# when the deterministic evidence comparison in app/diagnosis/retrieval.py
# conclusively proves it. metadata_filter_issue / knowledge_coverage stay
# BLOCKED (no filter/corpus evidence source exists).
RETRIEVAL_RULES: dict[str, DiagnosisRule] = {
    "retrieval.missing_evidence": DiagnosisRule(
        code="retrieval.missing_evidence",
        trigger_metrics=["context_recall", "context_precision"],
        trigger_condition=(
            "threshold failure on a retrieval metric AND deterministic gold-evidence "
            "containment comparison proves ALL gold evidence absent from retrieved contexts"
        ),
        evidence_contract="retrieval.missing_evidence.v1",
        severity_hint=DEFAULT_SEVERITY_HINTS["retrieval.missing_evidence"],
        recommendation_template="检索缺失：检查检索配置与知识库覆盖（top_k / filter / 排序）",
    ),
    "retrieval.top_k_issue": DiagnosisRule(
        code="retrieval.top_k_issue",
        trigger_metrics=["context_recall", "context_precision"],
        trigger_condition=(
            "threshold failure AND top_k datum valid AND cap saturated "
            "(retrieved_count == top_k) AND required gold evidence count > top_k "
            "AND gold evidence missing"
        ),
        evidence_contract="retrieval.top_k_issue.v1",
        severity_hint=DEFAULT_SEVERITY_HINTS["retrieval.top_k_issue"],
        recommendation_template="Top-K 不足：评估提高 top_k 或改进召回以覆盖所需证据",
    ),
}


# Post-MVP Phase 6 (evidence-first generation diagnosis): a threshold failure
# on a generation metric only TRIGGERS the check — the code is attributed ONLY
# when the deterministic numeric-claim comparison in
# app/diagnosis/generation.py conclusively proves it.
# generation.irrelevant_answer stays BLOCKED (relevance is semantic; no
# probabilistic/LLM proxy allowed this phase).
GENERATION_RULES: dict[str, DiagnosisRule] = {
    "generation.unsupported_claim": DiagnosisRule(
        code="generation.unsupported_claim",
        trigger_metrics=["faithfulness", "answer_relevancy"],
        trigger_condition=(
            "threshold failure on a generation metric AND numeric base-value "
            "equivalence proves ALL reliably-parsed answer claims absent from "
            "retrieved contexts"
        ),
        evidence_contract="generation.unsupported_claim.v1",
        severity_hint=DEFAULT_SEVERITY_HINTS["generation.unsupported_claim"],
        recommendation_template="答案断言缺乏召回证据支持：检查生成端 grounding 约束",
    ),
    "generation.partial_answer": DiagnosisRule(
        code="generation.partial_answer",
        trigger_metrics=["faithfulness", "answer_relevancy"],
        trigger_condition=(
            "threshold failure AND answer restates SOME but not ALL distinct "
            "reference numeric facts (0 < matched < N, N >= 2)"
        ),
        evidence_contract="generation.partial_answer.v1",
        severity_hint=DEFAULT_SEVERITY_HINTS["generation.partial_answer"],
        recommendation_template="答案相对参考要点不完整：检查要点覆盖与 Prompt 约束",
    ),
}


def rule_for_code(code: str) -> DiagnosisRule | None:
    return INTEGRITY_RULES.get(code) or RETRIEVAL_RULES.get(code) or GENERATION_RULES.get(code)
