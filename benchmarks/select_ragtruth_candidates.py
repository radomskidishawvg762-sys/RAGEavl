"""Select 10 distinct-question RAGTruth QA records (labelled preferred)."""

from __future__ import annotations

import json
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"


def main() -> None:
    sources = {}
    with (RAW / "ragtruth_source_info.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            sources[str(item["source_id"])] = item

    responses = []
    with (RAW / "ragtruth_response.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            responses.append(json.loads(line))

    seen_src: set[str] = set()
    chosen: list[dict] = []

    def take(response) -> None:
        sid = str(response["source_id"])
        if sid in seen_src:
            return
        src = sources.get(sid)
        if not src or src.get("task_type") != "QA":
            return
        info = src.get("source_info") or {}
        question = info.get("question") if isinstance(info, dict) else None
        if not isinstance(question, str) or not question.strip():
            return
        chosen.append({"response": response, "source": src})
        seen_src.add(sid)

    for response in responses:  # labelled first
        if response.get("labels"):
            take(response)
        if len(chosen) >= 10:
            break
    if len(chosen) < 10:
        for response in responses:
            take(response)
            if len(chosen) >= 10:
                break

    for i, entry in enumerate(chosen[:10]):
        response = entry["response"]
        source = entry["source"]
        info = source.get("source_info") or {}
        labels = response.get("labels") or []
        print("=" * 100)
        print(
            f"[{i}] resp_id={response['id']} source_id={response['source_id']} "
            f"model={response.get('model')} labels={len(labels)} "
            f"label_types={[label.get('label_type') for label in labels]}"
        )
        print("Q:", info.get("question"))
        passages = info.get("passages") if isinstance(info, dict) else ""
        print("PASSAGES:", (passages or "")[:2000])
        print("ANSWER:", (response.get("response") or "")[:800])


def main_write() -> None:
    import io
    import sys

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()


if __name__ == "__main__":
    main_write()
