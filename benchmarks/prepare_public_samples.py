"""Prepare small, auditable public benchmark samples.

This script creates reference questions and evidence only. It never fabricates
an external RAG answer. RAGTruth responses are kept as existing generated
answers, but remain separate because the corpus does not provide a reference
answer for every response.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
PROCESSED = ROOT / "processed"


def _table_context(table: list[list[Any]]) -> str:
    return "\n".join(" | ".join(str(cell) for cell in row) for row in table)


def prepare_tatqa(limit: int = 10) -> None:
    source = json.loads((RAW / "tatqa_dataset_dev.json").read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for item in source:
        paragraphs = {str(p["order"]): p["text"] for p in item.get("paragraphs", [])}
        contexts = [_table_context(item["table"]["table"])]
        contexts.extend(paragraphs.values())
        for question in item.get("questions", []):
            answer = question.get("answer")
            if isinstance(answer, list):
                reference_answer = "; ".join(str(value) for value in answer if str(value).strip())
            else:
                reference_answer = str(answer)
            evidence = [paragraphs[str(order)] for order in question.get("rel_paragraphs", []) if str(order) in paragraphs]
            if not evidence:
                evidence = contexts[:1]
            records.append({
                "question": question["question"],
                "reference_answer": reference_answer,
                "reference_contexts": evidence,
                "metadata": {
                    "source_dataset": "tatqa",
                    "source_sample_id": item["table"]["uid"],
                    "source_question_id": question["uid"],
                    "answer_type": question.get("answer_type"),
                    "scale": question.get("scale"),
                    "derivation": question.get("derivation"),
                    "evaluation_stage": "reference_only",
                },
            })
            if len(records) >= limit:
                break
        if len(records) >= limit:
            break
    payload = {"name": "tatqa_public_reference_v1", "domain": "general", "records": records}
    (PROCESSED / "tatqa_reference_10.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _ragtruth_contexts(source_info: dict[str, Any]) -> list[str]:
    value = source_info.get("source_info")
    if isinstance(value, str):
        return [value]
    if not isinstance(value, dict):
        return []
    passages = value.get("passages")
    if isinstance(passages, str):
        return [part.strip() for part in passages.split("\n\n") if part.strip()]
    return [json.dumps(value, ensure_ascii=False)]


def prepare_ragtruth(limit: int = 10) -> None:
    sources = {}
    with (RAW / "ragtruth_source_info.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            sources[str(item["source_id"])] = item

    records: list[dict[str, Any]] = []
    with (RAW / "ragtruth_response.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            response = json.loads(line)
            source = sources.get(str(response["source_id"]))
            if not source or source.get("task_type") != "QA":
                continue
            source_info = source.get("source_info") or {}
            question = source_info.get("question") if isinstance(source_info, dict) else None
            if not isinstance(question, str) or not question.strip():
                continue
            records.append({
                "question": question,
                "answer": response.get("response", ""),
                "contexts": _ragtruth_contexts(source),
                "reference_answer": None,
                "reference_contexts": None,
                "metadata": {
                    "source_dataset": "ragtruth",
                    "source_sample_id": response["id"],
                    "source_id": response["source_id"],
                    "task_type": response.get("task_type", source.get("task_type")),
                    "source": source.get("source"),
                    "model": response.get("model"),
                    "temperature": response.get("temperature"),
                    "quality": response.get("quality"),
                    "hallucination_labels": response.get("labels", []),
                    "evaluation_stage": "existing_generated_result",
                    "reference_status": "not_provided_by_source",
                },
            })
            if len(records) >= limit:
                break
    payload = {"name": "ragtruth_existing_results_v1", "domain": "general", "records": records}
    (PROCESSED / "ragtruth_existing_qa_10.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    PROCESSED.mkdir(parents=True, exist_ok=True)
    prepare_tatqa()
    prepare_ragtruth()
    print("prepared tatqa_reference_10.json and ragtruth_existing_qa_10.json")
