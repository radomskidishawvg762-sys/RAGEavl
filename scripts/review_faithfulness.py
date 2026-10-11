"""Local faithfulness audit. Default is offline; --live explicitly spends quota.

Frozen historical artifacts are read only. No API server, database or RAG service
is needed. Replay requires exact prompts, not just matching final scores.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from unittest.mock import patch

# RAGAS emits synchronous usage telemetry even for a scripted LLM. Disable it
# in this audit process so offline replay has no hidden external requests.
os.environ["RAGAS_DO_NOT_TRACK"] = "true"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.errors import ExtJudgeUnavailableError  # noqa: E402
from app.domain.schemas import EvaluationRecord  # noqa: E402
from app.engines.base import EvalParams  # noqa: E402
from app.engines.faithfulness_prompts import PROMPT_VERSION, prompt_manifest  # noqa: E402
from app.engines.judge import build_judge, judge_config_from_settings  # noqa: E402
from app.engines.ragas import RAGAS_VERSION, RagasEngine, metric_version_for  # noqa: E402
from scripts.analyze_judge_review import SUPPORTED_BASELINE, analyze  # noqa: E402

SOURCE = ROOT / "exports/judge-review-2026-10-11"
PROTOCOL = ROOT / "benchmarks/judge/faithfulness-2026-10-11.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")).hexdigest()


def source_hashes():
    return {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
            for name in ("results.json", "analysis.json", "trace.json")}


def jobs(data, protocol):
    result = []
    by_question = {c["input"]["question"]: c for c in data["cases"]}
    for version, run in data["runs"].items():
        for detail in run["details"]:
            case_id = by_question[detail["question"]]["case_id"]
            expected = int(version == "fixed" or case_id in SUPPORTED_BASELINE)
            result.append((f"candidate_{version}", case_id, expected, copy.deepcopy(detail)))
    for case in protocol["guardrails"]:
        result.append(("guardrails", case["case_id"], case["expected"], {
            "record_id": case["case_id"], "question": case["question"], "answer": case["answer"],
            "contexts": case["contexts"], "reference_answer": None, "reference_contexts": None,
            "metric_results": [], "diagnoses": [],
        }))
    return result


def select_jobs(data, protocol, only=None):
    available = jobs(data, protocol)
    if only is None:
        return available
    by_key = {f"{j[0]}/{j[1]}": j for j in available}
    selectors = only.split(",")
    if len(set(selectors)) != len(selectors) or any(s not in by_key for s in selectors):
        raise ValueError("--only must contain unique, existing version/case_id selectors")
    return [by_key[s] for s in selectors]


class BudgetJudge:
    def __init__(self, inner, limits):
        self.inner, self.limits = inner, limits
        self.calls, self.input_bytes = 0, 0
        self.trace = []

    def describe(self):
        return self.inner.describe()

    async def ainvoke(self, prompt):
        size = len(prompt.encode("utf-8"))
        if self.calls >= self.limits["max_calls"] or self.input_bytes + size > self.limits["max_input_bytes"]:
            raise ExtJudgeUnavailableError("local audit request budget exhausted", code="EXT_JUDGE_UNAVAILABLE")
        self.calls += 1
        self.input_bytes += size
        call = {"prompt": prompt}
        self.trace.append(call)
        try:
            response = await self.inner.ainvoke(prompt)
            call["response"] = response
            return response
        except ExtJudgeUnavailableError as e:
            call["error"] = {"message": e.detail, "code": e.code, "context": e.context}
            raise
        except asyncio.CancelledError:
            call["cancelled"] = True
            raise


class ReplayJudge:
    def __init__(self, trace, config):
        self.trace, self.config, self.index = trace, config, 0

    def describe(self):
        return {**self.config, "configured": True}

    async def ainvoke(self, prompt):
        if self.index >= len(self.trace):
            raise AssertionError("replay needs an unrecorded model call")
        call = self.trace[self.index]
        self.index += 1
        if call["prompt"] != prompt:
            raise AssertionError("prompt drift: replay is invalid")
        if call.get("cancelled"):
            raise TimeoutError()
        if "error" in call:
            raise ExtJudgeUnavailableError(call["error"]["message"], code=call["error"]["code"],
                                           context=call["error"].get("context"))
        return call["response"]


def record_of(detail):
    return EvaluationRecord(id=detail["record_id"], **{key: detail[key] for key in (
        "question", "answer", "contexts", "reference_answer", "reference_contexts")})


async def execute(output, protocol, data, *, only=None, compare_to=None, stop_on_negative_pass=False):
    cfg = judge_config_from_settings()
    # Never silently route to another model, version or endpoint. Credentials
    # remain environment-only; parameters come from the checked-in snapshot.
    for key in ("provider", "model", "model_version", "base_url"):
        if getattr(cfg, key) != protocol["judge_config"][key]:
            raise SystemExit(f"Configured {key} differs from the frozen review protocol")
    cfg = cfg.model_copy(update=protocol["judge_config"])
    if not cfg.api_key:
        raise SystemExit("Local judge credentials are missing")
    all_jobs = select_jobs(data, protocol, only)
    previous = {}
    if compare_to is not None:
        previous_manifest = read(compare_to / "manifest.json")
        if previous_manifest["judge_config_sha256"] != digest(cfg.public_dump()) or previous_manifest["prompt_version"] != PROMPT_VERSION:
            raise SystemExit("Repeat configuration/prompt differs from the reference capture")
        previous = {(c["version"], c["case_id"]): c for c in read(compare_to / "trace.json")}
        for version, case_id, _expected, detail in all_jobs:
            before = previous.get((version, case_id))
            if before is None or before["record"] != record_of(detail).model_dump():
                raise SystemExit(f"Repeat input differs from reference: {version}/{case_id}")
    judge = BudgetJudge(build_judge(cfg), protocol["limits"])
    engine = RagasEngine(judge=judge)
    result = copy.deepcopy(data)
    result["manual_labels"] = {}
    captured, cache = [], {}
    write(output / "manifest.json", {
        "captured_at": datetime.now(UTC).isoformat(), "source_sha256": source_hashes(),
        "judge_config": cfg.public_dump(), "judge_config_sha256": digest(cfg.public_dump()),
        "model_revision_status": protocol["model_revision_status"],
        "prompt_version": PROMPT_VERSION, "prompts": prompt_manifest(),
        "ragas_version": RAGAS_VERSION, "metric_version": metric_version_for("faithfulness"),
        "dependencies": {name: package_version(name) for name in (
            "ragas", "langchain-core", "langchain-community", "openai", "pydantic")},
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "limits": protocol["limits"], "method": "New metric outputs; historical scores retained separately",
        "selected_cases": [f"{j[0]}/{j[1]}" for j in all_jobs],
        "compare_to": str(compare_to) if compare_to is not None else None,
    })
    for i, (version, case_id, expected, detail) in enumerate(all_jobs, 1):
        if version not in result["runs"]:
            result["runs"][version] = {"details": []}
        result["manual_labels"].setdefault(version, {})[case_id] = expected
        if version == "guardrails":
            result["cases"].append({"case_id": case_id, "input": {"question": detail["question"]}})
        record = record_of(detail)
        key = digest({k: getattr(record, k) for k in ("question", "answer", "contexts")})
        if key not in cache:
            judge.trace = []
            metric, = await engine.evaluate(record, ["faithfulness"], EvalParams(threshold=0.9))
            cache[key] = (metric.model_dump(), copy.deepcopy(judge.trace))
        raw, trace = copy.deepcopy(cache[key])
        raw["record_id"] = record.id
        captured.append({"version": version, "case_id": case_id, "input_sha256": key,
                         "record": record.model_dump(), "metric": copy.deepcopy(raw), "calls": trace})
        raw["name"] = raw.pop("metric_name")
        detail["case_id"] = case_id
        detail["metric_results"] = [raw]
        detail["diagnoses"] = []
        result["runs"][version]["details"].append(detail)
        write(output / "results.json", result)
        write(output / "trace.json", captured)
        print(f"{i}/{len(all_jobs)} {version}/{case_id}: score={raw['score']} calls={judge.calls}", flush=True)
        if stop_on_negative_pass and expected == 0 and raw["passed"] is True:
            write(output / "stop.json", {"reason": "unsupported case passed", "case_id": case_id,
                                         "metric": raw, "labels_changed": False})
            raise SystemExit(f"STOP: negative case {case_id} passed; labels unchanged")
        if judge.calls >= protocol["limits"]["max_calls"]:
            raise SystemExit("Audit budget exhausted; partial artifacts retained")
    analysis = analyze(result)
    analysis["method"] = "Frozen historical vs new live faithfulness; diagnosis replay uses evidence contracts"
    write(output / "analysis.json", analysis)
    write(output / "budget.json", {"calls": judge.calls, "input_bytes": judge.input_bytes,
                                  "max_output_tokens": judge.calls * cfg.max_tokens})
    if previous:
        comparison = []
        for c in captured:
            old = previous[c["version"], c["case_id"]]
            expected = result["manual_labels"][c["version"]][c["case_id"]]
            before, after = old["metric"]["score"], c["metric"]["score"]
            comparison.append({"case_id": c["case_id"], "manual_expected": expected,
                "before_score": before, "after_score": after, "score_identical": before == after,
                "before_matches": (before >= 0.9) == bool(expected) if before is not None else None,
                "after_matches": (after >= 0.9) == bool(expected) if after is not None else None,
                "input_identical": old["record"] == c["record"], "error": c["metric"]["error"]})
        write(output / "repeat-comparison.json", {"cases": comparison,
            "before_matches": sum(c["before_matches"] is True for c in comparison),
            "after_matches": sum(c["after_matches"] is True for c in comparison),
            "after_usable": sum(c["after_score"] is not None for c in comparison),
            "identical_scores": sum(c["score_identical"] for c in comparison)})


async def replay(path):
    manifest = read(path / "manifest.json")
    if manifest["prompt_version"] != PROMPT_VERSION:
        raise SystemExit("Prompt revision differs: use the source revision captured in the manifest")
    with patch("socket.socket.connect", side_effect=AssertionError("offline replay forbids network")) as network:
        for case in read(path / "trace.json"):
            judge = ReplayJudge(case["calls"], manifest["judge_config"])
            record = EvaluationRecord(**case["record"])
            metric, = await RagasEngine(judge=judge).evaluate(record, ["faithfulness"], EvalParams(threshold=0.9))
            expected = dict(case["metric"])
            # Initial local capture used the API field name. Accept only this
            # lossless spelling change; no scores, errors or prompts are altered.
            if "name" in expected:
                expected["metric_name"] = expected.pop("name")
            if metric.model_dump() != expected or judge.index != len(judge.trace):
                raise AssertionError(f"replay diverged: {case['version']}/{case['case_id']}")
        network.assert_not_called()
    print("Exact prompt/output replay passed; no network calls or score updates")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", type=Path, metavar="CAPTURE_DIRECTORY")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--only", help="Comma-separated version/case_id selectors; no other cases run")
    parser.add_argument("--compare-to", type=Path, help="Reference capture with identical config and inputs")
    parser.add_argument("--stop-on-negative-pass", action="store_true")
    args = parser.parse_args()
    if args.replay:
        asyncio.run(replay(args.replay))
        return
    protocol, data = read(PROTOCOL), read(SOURCE / "results.json")
    try:
        selected = select_jobs(data, protocol, args.only)
    except ValueError as e:
        parser.error(str(e))
    if protocol["prompt_version"] != PROMPT_VERSION or RAGAS_VERSION != "0.4.3":
        raise SystemExit("Frozen prompt or RAGAS protocol differs from installed source")
    output = args.output or ROOT / "exports" / ("faithfulness-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))
    # Never overwrite a prior capture, even through an explicit --output path.
    output.mkdir(parents=True, exist_ok=False)
    frozen_hashes = source_hashes()
    write(output / "historical-analysis.json", analyze(data))
    if args.live:
        asyncio.run(execute(output, protocol, data, only=args.only, compare_to=args.compare_to,
                            stop_on_negative_pass=args.stop_on_negative_pass))
    else:
        write(output / "preflight.json", {"source_sha256": frozen_hashes, "prompts": prompt_manifest(),
            "protocol": protocol, "review_records": len(selected), "network_calls": 0})
        print("Offline historical analysis/preflight only; candidate accuracy requires --live")
    assert source_hashes() == frozen_hashes, "Historical artifacts changed"
    print(output)


if __name__ == "__main__":
    main()
