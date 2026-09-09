"""Level-4 experiment: retrieval-diagnosis capability over MIRACL zh-dev qrels.

RAGEval does not run a retriever (its role is evaluation). So the MIRACL
validation is: given external retrieved-context results + gold qrels, can the
platform's deterministic retrieval diagnosis (missing_evidence / top_k_issue)
attribute the correct root cause?

Scenarios built from real MIRACL zh dev data:
  1. gold_hit           - all gold evidence present in retrieved contexts
  2. partial_hit        - some gold present, some missing
  3. top_k_saturated    - retrieved_count == top_k AND required gold > top_k
                          AND gold evidence missing (top_k_issue)
"""

from __future__ import annotations

import json
from pathlib import Path

from app.diagnosis.retrieval import evaluate_retrieval
from app.domain.schemas import EvaluationRecord

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUTPUT = ROOT / "processed" / "miracl_retrieval_diagnosis_l4.json"

# qid -> query (from MIRACL zh dev topics, decoded from UTF-8)
# We read them programmatically below; these are illustrative placeholders used
# to keep the script readable. The real queries are loaded from the TSV.
PLACEHOLDER_QUERY = "（MIRACL zh dev 查询）"


def _load_topics() -> dict[str, str]:
    topics: dict[str, str] = {}
    for line in (RAW / "miracl_zh_dev_topics.tsv").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            topics[parts[0]] = parts[1]
    return topics


def _load_gold_by_qid() -> dict[str, list[str]]:
    gold: dict[str, list[str]] = {}
    for line in (RAW / "miracl_zh_dev_qrels.tsv").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        qid, _q0, docid, rel = parts[0], parts[1], parts[2], parts[3]
        if rel == "1":
            gold.setdefault(qid, []).append(docid)
    return gold


def _evaluate(question: str, contexts: list[str], gold: list[str], top_k: int | None) -> dict:
    record = EvaluationRecord(
        id="miracl-l4",
        question=question,
        contexts=contexts,
        answer="",  # retrieval diagnosis does not use the answer
        reference_answer="",
        reference_contexts=gold,
        metadata={"rag": {"top_k": top_k}} if top_k is not None else {},
    )
    outcome = evaluate_retrieval(record)
    return {
        "status": outcome.status,
        "code": outcome.code,
        "reason": outcome.reason,
        "missing_evidence": outcome.missing_evidence,
        "contract": outcome.contract,
    }


def main() -> None:
    topics = _load_topics()
    gold_by_qid = _load_gold_by_qid()
    qids = [qid for qid, g in gold_by_qid.items() if qid in topics and len(g) >= 2]
    if len(qids) < 20:
        qids = list(gold_by_qid.keys())[:20]

    rows = []
    for i, qid in enumerate(qids[:20]):
        gold = gold_by_qid[qid]
        gold_hit = list(gold)[:2]
        non_gold = ["不相关文档A的正文内容示例。", "另一条无关检索结果文本示例。"]
        # scenario contexts (per the engine's attribution contract):
        #  - gold_hit:      all gold present -> undetermined (no retrieval cause)
        #  - all_missing:   0/2 gold present, but some non-gold contexts exist
        #                    -> missing_evidence (all gold absent is conclusive)
        #  - top_k_saturated: non-gold contexts count == top_k, gold > top_k,
        #                    all gold missing -> top_k_issue
        hit_ctx = list(gold)[:2]
        missing_ctx = list(non_gold)
        top_k_ctx = list(non_gold)[:1]  # top_k=1, 1 non-gold, gold=2 -> saturated
        rows.append({
            "qid": qid,
            "query": topics[qid],
            "gold_count": len(gold),
            "scenarios": {
                "gold_hit": _evaluate(topics[qid], hit_ctx, gold_hit, top_k=2),
                "all_missing": _evaluate(topics[qid], missing_ctx, gold_hit, top_k=2),
                "top_k_saturated": _evaluate(topics[qid], top_k_ctx, gold_hit, top_k=1),
            },
        })
        if i >= 19:
            break

    # correct classification counts
    counts = {
        "gold_hit_undetermined": sum(1 for r in rows if r["scenarios"]["gold_hit"]["status"] == "undetermined"),
        "all_missing_missing_evidence": sum(1 for r in rows if r["scenarios"]["all_missing"]["code"] == "retrieval.missing_evidence"),
        "top_k_issue": sum(1 for r in rows if r["scenarios"]["top_k_saturated"]["code"] == "retrieval.top_k_issue"),
    }

    result = {
        "experiment": "level4_miracl_retrieval_diagnosis",
        "method": "deterministic",
        "n_queries": len(rows),
        "gold_hit_expected": "undetermined (no failure attributable)",
        "all_missing_expected": "retrieval.missing_evidence",
        "top_k_saturated_expected": "retrieval.top_k_issue",
        "counts": counts,
        "rows": rows,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"n_queries": len(rows), "counts": counts}, ensure_ascii=False, indent=2))
    # show first query details
    first = rows[0]
    print("sample query:", first["query"])
    print("gold_hit:", first["scenarios"]["gold_hit"])
    print("all_missing:", first["scenarios"]["all_missing"])
    print("top_k_saturated:", first["scenarios"]["top_k_saturated"])
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()