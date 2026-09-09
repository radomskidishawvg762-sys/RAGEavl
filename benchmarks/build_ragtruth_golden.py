"""Build RAGTruth golden QA dataset (10 distinct questions, labelled).

Reference answers are CURIATED from the source passages by the assistant and are
NOT part of the official RAGTruth corpus. They are flagged in metadata so the
reader knows they are a human-curated draft pending review. RAGTruth answers and
contexts are preserved verbatim and also copied into metadata for the
GoldenRunMetadataAdapter (answer/contexts must live in metadata for replay).
"""

from __future__ import annotations

import json
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
PROCESSED = Path(__file__).resolve().parent / "processed"
OUTPUT = PROCESSED / "ragtruth_golden_qa_10.json"

SELECTED = [
    "14292", "14293", "14294", "14295", "14296",
    "14297", "14298", "14299", "14300", "14301",
]

# (question, model) pairs to disambiguate which response to use.
# source_id -> (reference_answer, [reference_context indices 1-based])
CURATED = {
    "14292": (
        "Butcher shops and their phone numbers: Butcher Shop - Hayward, 826 B Street, Hayward CA 94541, (510) 889-8690; "
        "Butcher Shop - Lakefield, 212 Main Street, Lakefield MN 56150, (507) 662-6281; "
        "The Local Butcher Shop (510) 845-6328; Boones Butcher Shop (502) 348-3668; "
        "Ye Ole Butcher Shop (972) 423-1848; J Ms Butcher Shop (865) 483-9228.",
        [1, 2, 3],
    ),
    "14293": (
        "Water-saving tips: take shorter showers; replace the showerhead with an ultra-low-flow version; "
        "use the minimum water needed for a bath by filling the tub only 1/3 full; "
        "collect the water that runs while waiting for the shower to heat up (shower bucket) and use it to flush the toilet or water plants; "
        "turn off the tap while brushing teeth and washing hands; water lawns in the early morning or later evening in 20-minute intervals; "
        "use a broom instead of a hose to clean driveways and sidewalks; bathe pets outdoors; "
        "install a shut-off nozzle on the hose; direct downspouts towards shrubs and trees.",
        [1, 2, 3],
    ),
    "14294": (
        "To safely clean a computer screen: lightly wipe the screen with a dry, clean microfiber cloth (never paper or kitchen towels); "
        "always clean the screen powered off; for heavier cleaning, mix 1 part isopropyl alcohol with 1 part distilled water in a spray bottle, "
        "spray a lint-free cloth (not the screen) and wipe in one direction; "
        "clean the edges of an LCD screen with a clean cotton ball or swab; "
        "never use window or abrasive cleaners and do not rub hard (it can damage pixels).",
        [1, 2, 3],
    ),
    "14295": (
        "In Britain, single cream has between 10% and 12% butterfat and is a thin liquid similar to half and half; "
        "double cream contains 48% butterfat and is thicker. Single cream is too thin to whip; "
        "double cream whips quickly into soft and then hard peaks. "
        "In cooking, double cream binds with flour to produce a thicker sauce, so less is generally needed.",
        [1, 2, 3],
    ),
    "14296": (
        "To soak off gel polish: soak a cotton ball with acetone remover and place it on the fingernail; "
        "wrap the finger with the cotton ball in foil, squeezing it tight so it stays in contact; "
        "remove the foil and push the polish off using an orange stick or cuticle pusher; "
        "do not use anything too sharp; repeat for each nail. "
        "Gelish soak-off gel cures in an LED lamp in 30 seconds or 2 minutes in a UV lamp and soaks off in 10-15 minutes.",
        [1, 2, 3],
    ),
    "14297": (
        "To remove a row in Microsoft Word: right-click in a cell inside the row you want to delete, "
        "choose Delete Cells from the context menu, select the 'Delete entire row' option and hit OK. "
        "You can delete the row content while keeping the structural row, or delete the row and its content together.",
        [2, 3],
    ),
    "14298": (
        "Foods rich in potassium include bananas, apricots, prunes, dates, cantaloupe, watermelon, strawberries, salmon, beans, turkey, fish, peas, greens, spinach and tomatoes. "
        "Foods rich in calcium include milk, cheese, yogurt, dark leafy vegetables (kale, turnip greens, broccoli, brussels sprouts, cabbage) and canned fish with bones such as salmon, sardines and mackerel. "
        "Magnesium is widely distributed in green leafy vegetables such as spinach, legumes, nuts, seeds and whole grains.",
        [1, 2, 3],
    ),
    "14299": (
        "The passages discuss the choice between entering the workforce and going to graduate school. "
        "Graduate school is expensive and time consuming but allows further education in a specific field; "
        "graduates who find work can earn more (median annual salary plus bonus for a fresh MBA is $105,000). "
        "Some students work and pursue grad school at the same time, or volunteer part-time around their study schedule.",
        [1, 2, 3],
    ),
    "14300": (
        "Automotive technicians can be paid hourly, by commission, or on a flat-rate/flag-rate basis; "
        "whether they are entitled to overtime depends on where they work, how they are paid, how much they make and the state. "
        "Alaska has the highest average pay at about $23.70 per hour or $49,400 per year; "
        "automotive technicians in aerospace products and parts manufacturing earn about $32 per hour or $66,300 per year on average.",
        [1, 2],
    ),
    "14301": (
        "To plow a field: find the center of the field front and back and mark it off with stakes; "
        "line up the tractor on the stakes and plow as straight as possible; "
        "turn around at the end and plow back up the field with the first bottom completely overlapping so the middle is higher; "
        "at the top, line up the wheel in the furrow so the first bottom covers the furrow.",
        [1, 2, 3],
    ),
}


def _split_passages(passages_text: str) -> list[str]:
    chunks = [chunk.strip() for chunk in (passages_text or "").split("\n\n") if chunk.strip()]
    return chunks


def build() -> None:
    sources = {}
    with (RAW / "ragtruth_source_info.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            sources[str(item["source_id"])] = item

    responses_by_source: dict[str, list[dict]] = {}
    with (RAW / "ragtruth_response.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            responses_by_source.setdefault(str(item["source_id"]), []).append(item)

    records = []
    for source_id in SELECTED:
        source = sources.get(source_id)
        if not source:
            raise ValueError(f"source {source_id} missing")
        info = source.get("source_info") or {}
        question = info.get("question") if isinstance(info, dict) else None
        passages = info.get("passages") if isinstance(info, dict) else ""
        responses = responses_by_source.get(source_id, [])
        labelled = next((r for r in responses if r.get("labels")), None)
        response = labelled or (responses[0] if responses else None)
        if not response or not isinstance(question, str):
            raise ValueError(f"source {source_id} unusable")
        curated_answer, ref_idx = CURATED[source_id]
        passages_list = _split_passages(passages)
        reference_contexts = [passages_list[i - 1] for i in ref_idx if i - 1 < len(passages_list)]
        if not reference_contexts:
            raise ValueError(f"source {source_id} reference_contexts empty")
        records.append({
            "question": question,
            "answer": response.get("response", ""),
            "contexts": passages_list,
            "reference_answer": curated_answer,
            "reference_contexts": reference_contexts,
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
                # GoldenRunMetadataAdapter reads answer/contexts from metadata
                "answer": response.get("response", ""),
                "contexts": passages_list,
            },
        })

    payload = {
        "name": "ragtruth_golden_qa_v1",
        "domain": "general",
        "records": records,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(records)} records")


if __name__ == "__main__":
    build()