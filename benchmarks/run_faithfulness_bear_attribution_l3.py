"""Level-3 attribution experiment: is the faithfulness false-negative a judge
problem or a RAGAS claim-decomposition problem?

Record: RAGTruth source 14292 (butcher shop phone number), mistral-7B answer.
Human label: Evident Baseless Info - the answer introduces a location named
"bear" that is absent from the retrieved contexts. Faithfulness scored 1.0.

Controlled variants of the SAME answer against the SAME contexts:
  A (bear present)  : original answer, contains the baseless "bear" content.
  B (bear removed)  : same answer with the two "bear" sentences deleted.

If faithfulness drops in A relative to B (or A is not 1.0) then the metric can
detect the baseless claim given the judge; if both are 1.0 then the metric /
judge pipeline systematically misses "introduced new information" spans.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.judge import JudgeConfig, build_judge
from app.engines.ragas import RagasEngine

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "processed" / "faithfulness_bear_attribution_l3.json"

QUESTION = "butcher shop phone number"
CONTEXTS = [
    "Butcher Shop - Hayward 826 B Street, Hayward CA 94541 Phone Number: (510) 889-8690",
    "Butcher Shop - Lakefield 212 Main Street, Lakefield MN 56150 Phone Number: (507) 662-6281",
    "The Local Butcher Shop phone number: (510) 845-6328. Boones Butcher Shop phone number: (502) 348-3668. "
    "Ye Ole Butcher Shop phone number: (972) 423-1848. J Ms Butcher Shop phone number: (865) 483-9228.",
]

ANSWER_A = (
    "Based on the given passages, there are several different phone numbers associated with butcher shops. "
    'However, none of them are for a specific location named "bear" or have any indication of being related to bears. '
    "Therefore, it is unable to determine if there is a butcher shop phone number specifically for \"bear\"."
)

ANSWER_B = (
    "Based on the given passages, there are several different phone numbers associated with butcher shops. "
    "The passages list phone numbers for the butcher shops mentioned above."
)


def _record(answer: str) -> EvaluationRecord:
    return EvaluationRecord(
        id="bear-attribution",
        question=QUESTION,
        contexts=CONTEXTS,
        answer=answer,
        reference_answer=None,
        reference_contexts=None,
    )


async def _score(engine: RagasEngine, record: EvaluationRecord) -> dict:
    results = await engine.evaluate(record, ["faithfulness"], EvalParams())
    if not results:
        return {"score": None, "status": "no_result"}
    r = results[0]
    return {"score": r.score, "status": "error" if r.error else "completed",
            "error": (r.error or {}).get("code")}


def main() -> None:
    import os

    key = None
    env_file = Path(os.getcwd()) / ".env.local"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("JUDGE_API_KEY="):
                key = line.split("=", 1)[1].strip()
    cfg = JudgeConfig(
        provider="openai",
        model=os.environ.get("JUDGE_MODEL", "glm-5.3-flash"),
        base_url=os.environ.get("JUDGE_BASE_URL", "https://opencode.ai/zen/go/v1"),
        api_key=key,
        temperature=0,
        max_tokens=1024,
        timeout=300,
        retry=3,
    )
    judge = build_judge(cfg)
    engine = RagasEngine(judge=judge)

    results = asyncio.run(_score(engine, _record(ANSWER_A)))
    results_b = asyncio.run(_score(engine, _record(ANSWER_B)))

    payload = {
        "experiment": "level3_faithfulness_bear_attribution",
        "judge": {"provider": "openai", "model": cfg.model, "base_url": cfg.base_url, "timeout": cfg.timeout},
        "record": {"question": QUESTION, "contexts": CONTEXTS},
        "variant_a_bear_present": {"answer": ANSWER_A, "faithfulness": results["score"], "status": results["status"]},
        "variant_b_bear_removed": {"answer": ANSWER_B, "faithfulness": results_b["score"], "status": results_b["status"]},
        "conclusion": (
            "If A==B==1.0 -> systematic miss of introduced-new-info spans (judge+metric limitation). "
            "If A<B or A<1.0 -> the judge can detect the baseless span when it is present."
        ),
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()