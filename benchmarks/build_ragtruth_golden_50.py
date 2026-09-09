"""Build a 50-record RAGTruth golden QA dataset (distinct questions).

Labelled responses are preferred; unlabelled ones fill the rest. Reference
answers are CURIATED by the assistant from source passages (flagged pending
review). Contexts are deduplicated and truncated to bound RAGAS latency.
Answer/contexts are preserved in metadata for the GoldenRunMetadataAdapter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RAW = Path(__file__).resolve().parent / "raw"
PROCESSED = Path(__file__).resolve().parent / "processed"
OUTPUT = PROCESSED / "ragtruth_golden_qa_50.json"

MAX_CONTEXT_CHARS = 800
N = 50


def _split_passages(text: str) -> list[str]:
    return [c.strip() for c in (text or "").split("\n\n") if c.strip()]


def _dedup_truncate(chunks: list[str], max_chars: int) -> list[str]:
    out: list[str] = []
    for c in chunks:
        if c not in out:
            out.append(c[:max_chars])
    return out


def _strip_passage_prefix(passage: str) -> str:
    """Remove a leading 'passage N:' label so the ordinal (e.g. '1') is never
    parsed as a numeric value inside the curated reference."""
    text = passage.strip()
    import re as _re

    return _re.sub(r"^passage\s+\d+\s*:\s*", "", text, flags=_re.IGNORECASE).strip()


def _curate_reference(source: dict[str, Any], passages: list[str]) -> str:
    """Best-effort factual reference from passages (assistant-curated draft).

    Concatenates the leading distinctive sentences of each passage so the
    reference reflects the source content without inventing claims. Flagged as
    a draft pending user review, not an official corpus label. The 'passage N:'
    label is stripped so ordinals are not parsed as numeric values.
    """
    parts: list[str] = []
    for passage in passages:
        cleaned = _strip_passage_prefix(passage)
        sentences = [s.strip() for s in cleaned.replace("\n", " ").split(".") if s.strip()]
        lead = ".".join(sentences[:2])
        if lead:
            parts.append(lead + ".")
    return " ".join(parts)[:1200]


def build() -> None:
    sources: dict[str, dict] = {}
    with (RAW / "ragtruth_source_info.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            sources[str(item["source_id"])] = item

    responses_by_source: dict[str, list[dict]] = {}
    with (RAW / "ragtruth_response.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            responses_by_source.setdefault(str(item["source_id"]), []).append(item)

    seen: set[str] = set()
    chosen: list[tuple[str, dict]] = []  # (source_id, response)

    def take(source_id: str, response: dict) -> bool:
        if source_id in seen:
            return False
        src = sources.get(source_id)
        if not src or src.get("task_type") != "QA":
            return False
        info = src.get("source_info") or {}
        question = info.get("question") if isinstance(info, dict) else None
        if not isinstance(question, str) or not question.strip():
            return False
        seen.add(source_id)
        chosen.append((source_id, response))
        return True

    # pass 1: labelled responses, one per source
    for source_id, responses in responses_by_source.items():
        labelled = next((r for r in responses if r.get("labels")), None)
        if labelled:
            take(source_id, labelled)
        if len(chosen) >= N:
            break
    # pass 2: any remaining distinct sources
    if len(chosen) < N:
        for source_id, responses in responses_by_source.items():
            take(source_id, responses[0])
            if len(chosen) >= N:
                break

    records: list[dict] = []
    for source_id, response in chosen[:N]:
        src = sources[source_id]
        info = src.get("source_info") or {}
        question = info.get("question")
        passages = _split_passages(info.get("passages") if isinstance(info, dict) else "")
        contexts = _dedup_truncate(passages, MAX_CONTEXT_CHARS)
        ref_answer = _curate_reference(src, contexts)
        records.append({
            "question": question,
            "answer": response.get("response", ""),
            "contexts": contexts,
            "reference_answer": ref_answer,
            "reference_contexts": contexts[:2],
            "metadata": {
                "source_dataset": "ragtruth",
                "source_id": source_id,
                "source_sample_id": response["id"],
                "model": response.get("model"),
                "temperature": response.get("temperature"),
                "quality": response.get("quality"),
                "hallucination_labels": response.get("labels", []),
                "reference_source": "curated_from_source_passages_by_assistant",
                "reference_verification": "pending_user_review",
                "evaluation_stage": "existing_generated_result_with_curated_reference",
                "context_trimmed": True,
                "max_context_chars": MAX_CONTEXT_CHARS,
                "answer": response.get("response", ""),
                "contexts": contexts,
            },
        })

    payload = {"name": "ragtruth_golden_qa_50", "domain": "general", "records": records}
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    labelled = sum(1 for r in records if r["metadata"]["hallucination_labels"])
    print(f"wrote {OUTPUT} records={len(records)} labelled={labelled}")


if __name__ == "__main__":
    build()