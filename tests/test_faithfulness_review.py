"""Audit tooling must preserve labels and fail closed on replay/budget drift."""
from __future__ import annotations

import pytest

from app.core.errors import ExtJudgeUnavailableError
from scripts.analyze_judge_review import analyze
from scripts.review_faithfulness import BudgetJudge, ReplayJudge, select_jobs


def test_selection_never_falls_back_to_full_live_audit(monkeypatch):
    available = [("candidate_baseline", "B2-ret-001", 0, {}), ("guardrails", "G-period", 0, {})]
    monkeypatch.setattr("scripts.review_faithfulness.jobs", lambda *_: available)
    assert select_jobs({}, {}, "guardrails/G-period") == [available[1]]
    with pytest.raises(ValueError):
        select_jobs({}, {}, "missing/case")
    with pytest.raises(ValueError):
        select_jobs({}, {}, "guardrails/G-period,guardrails/G-period")


def test_analyzer_cannot_overwrite_original_review(monkeypatch):
    from scripts import analyze_judge_review

    monkeypatch.setattr("sys.argv", ["analyze", "--output", str(analyze_judge_review.OUT / "analysis.json")])
    with pytest.raises(SystemExit) as error:
        analyze_judge_review.main()
    assert error.value.code == 2


def test_duplicate_questions_use_case_identity_for_labels():
    cases, details = [], []
    for case_id, answer, score in [("supported", "收入230元", 1), ("unsupported", "利润230元", 0)]:
        cases.append({"case_id": case_id, "input": {"question": "金额是多少？"}})
        details.append({"record_id": case_id, "question": "金额是多少？", "answer": answer,
            "contexts": ["收入230元"], "reference_answer": None, "reference_contexts": None,
            "diagnoses": [], "metric_results": [{"name": "faithfulness", "category": "generation",
                "score": score, "threshold": 0.9, "passed": bool(score), "metric_version": "test",
                "comparison_basis": None, "error": None}]})
    data = {"cases": cases, "runs": {"guardrails": {"details": details}},
            "manual_labels": {"guardrails": {"supported": 1, "unsupported": 0}}}
    reviewed = analyze(data)["judge_calibration"]["guardrails"]
    assert reviewed["usable_scores"] == reviewed["matches_manual_label"] == 2
    assert reviewed["unsupported_passes"] == reviewed["false_low_scores"] == 0
    assert [c["manual_expected"] for c in reviewed["cases"]] == [1, 0]


class CountingJudge:
    calls = 0

    def describe(self):
        return {"configured": True}

    async def ainvoke(self, prompt):
        self.calls += 1
        return "response"


@pytest.mark.parametrize("limits,first", [
    ({"max_calls": 1, "max_input_bytes": 100}, "ok"),
    ({"max_calls": 10, "max_input_bytes": 6}, "中文"),
])
async def test_live_budget_stops_before_extra_request(limits, first):
    inner = CountingJudge()
    judge = BudgetJudge(inner, limits)
    assert await judge.ainvoke(first) == "response"
    with pytest.raises(ExtJudgeUnavailableError, match="budget exhausted"):
        await judge.ainvoke("next")
    assert inner.calls == judge.calls == 1


async def test_replay_rejects_prompt_drift_without_network():
    judge = ReplayJudge([{"prompt": "frozen prompt", "response": "frozen response"}], {})
    with pytest.raises(AssertionError, match="prompt drift"):
        await judge.ainvoke("changed prompt")


async def test_replay_returns_frozen_response():
    judge = ReplayJudge([{"prompt": "frozen prompt", "response": "frozen response"}], {})
    assert await judge.ainvoke("frozen prompt") == "frozen response"
    assert judge.index == 1
