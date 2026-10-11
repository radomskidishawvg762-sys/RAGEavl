"""20-case live Judge audit against an isolated API and a labelled local HTTP RAG fixture.

Start: python scripts/serve_review.py --with-judge
Run:   python scripts/run_judge_review.py --live
Never run alongside PostgreSQL pytest (which truncates the dedicated test DB).
Paid calls require --live. At most two 20-record runs; no automatic rerun.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "exports/judge-review-2026-10-11"


def cases():
    source = json.loads((ROOT / "benchmarks/v0.1/cases.json").read_text(encoding="utf-8"))
    selected = [c for c in source["cases"] if c.get("kind") == "integrity" and c.get("metric") == "numerical_consistency"]
    assert len(selected) == 15
    for c in selected:
        c["input"]["question"] = f"【{c['case_id']}】" + c["input"]["question"]
    for code, mode in [("HTTP-404", "not_found"), ("HTTP-JSON", "invalid_json"),
                       ("HTTP-CONTEXTS", "invalid_contexts"), ("HTTP-500", "server_error"),
                       ("HTTP-EMPTY", "empty_contexts")]:
        selected.append({"case_id": code, "fixture_mode": mode,
                         "input": {"question": f"【{code}】营业收入是多少？", "reference_answer": "100元",
                                   "reference_contexts": ["营业收入100元"], "answer": "100元", "contexts": ["营业收入100元"]},
                         "expected": {"comparison_type": "match", "score": 1.0}})
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow the bounded live Judge calls")
    parser.add_argument("--backend", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly enable paid model calls")
    OUT.mkdir(parents=True, exist_ok=True)
    sample = cases()
    result = {"rag_source": "local HTTP TEST FIXTURE, not a production RAG system",
              "benchmark_source": "benchmarks/v0.1/cases.json (15 numerical cases) + 5 HTTP boundary cases",
              "cases": sample, "runs": {}, "checks": []}

    def save():
        (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name, ok, detail=None):
        entry = {"name": name, "ok": bool(ok), "detail": detail}
        result["checks"].append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        save()
        if not ok:
            raise RuntimeError(name)

    client = httpx.Client(base_url=args.backend, timeout=70, trust_env=False)

    def request(method, path, **kw):
        response = client.request(method, path, **kw)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
        return response.json()

    health = client.get("/api/health")
    check("isolated test database", health.headers.get("x-rageval-review") == "isolated-test-database")
    probe = request("POST", "/api/settings/judge/test")
    result["probe"] = {k: probe.get(k) for k in ("provider", "model", "test_status", "latency_ms", "error_code", "error_category", "message")}
    check("real model probe succeeds", probe["test_status"] == "success", result["probe"])
    # The audit limits retry count and concurrency; never alters the development server.
    request("PUT", "/api/settings/judge", json={"provider": probe["provider"], "model": probe["model"],
                                                 "timeout": 60, "retry": 1, "max_tokens": 1024})
    by_question = {c["input"]["question"]: c for c in sample}
    state = {"version": "baseline", "calls": []}

    class RagFixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            case = by_question[data["question"]]
            state["calls"].append({"case_id": case["case_id"], "version": state["version"]})
            item = case["input"]
            status, payload = 200, None
            if state["version"] == "baseline":
                mode = case.get("fixture_mode")
                if mode == "not_found":
                    status, payload = 404, {"error": "fixture sample missing"}
                elif mode == "server_error":
                    status, payload = 500, {"error": "fixture service failure"}
                elif mode == "invalid_json":
                    payload = "this is intentionally invalid JSON"
                elif mode == "invalid_contexts":
                    payload = {"answer": "100元", "contexts": [123]}
                else:
                    payload = {"answer": item.get("answer", ""), "contexts": [] if mode == "empty_contexts" else item.get("contexts", [])}
            else:
                payload = {"answer": item.get("reference_answer") or item.get("answer", ""),
                           "contexts": item.get("reference_contexts") or item.get("contexts", [])}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            self.wfile.write(body.encode("utf-8"))

    server = ThreadingHTTPServer(("127.0.0.1", 0), RagFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    active_run = None
    try:
        project = request("POST", "/api/projects", json={"name": f"review-judge-{time.time_ns()}", "domain": "general"})
        records = []
        for case in sample:
            item = case["input"]
            record = {"question": item["question"], "metadata": {"audit_case_id": case["case_id"]}}
            if item.get("reference_answer"):
                record["reference_answer"] = item["reference_answer"]
            if item.get("reference_contexts"):
                record["reference_contexts"] = item["reference_contexts"]
            records.append(record)
        dataset = request("POST", f"/api/projects/{project['id']}/datasets:import", json={"name": "judge-review-20",
                           "domain": "general", "duplicate_policy": "strict", "records": records})
        result.update(project_id=project["id"], dataset_id=dataset["id"])
        golden_before = request("GET", f"/api/datasets/{dataset['id']}/records?page_size=100")
        yaml = f"""profile:
  name: judge_review_20
metrics:
  numerical_consistency: {{enabled: true, threshold: 0.9, weight: 1}}
  temporal_consistency: {{enabled: false}}
  entity_consistency: {{enabled: false}}
  faithfulness: {{enabled: true, threshold: 0.9, weight: 1}}
  answer_relevancy: {{enabled: false}}
  context_recall: {{enabled: false}}
  context_precision: {{enabled: false}}
pipeline:
  engines: [integrity, ragas]
  diagnosis: {{enabled: true}}
rag_input:
  mode: http
  url: http://127.0.0.1:{server.server_port}/rag
  timeout: 2
  retry: 1
quality_gate: null
"""
        cfg = request("POST", "/api/configs/import", json={"project_id": project["id"], "domain": "general", "yaml": yaml})
        expected_errors = {"HTTP-404": "EXT_RAG_INPUT_NOT_FOUND", "HTTP-JSON": "EXT_RAG_ADAPTER_PARSE_ERROR",
                           "HTTP-CONTEXTS": "EXT_RAG_ADAPTER_PARSE_ERROR", "HTTP-500": "EXT_RAG_ADAPTER_HTTP_ERROR"}
        for version in ("baseline", "fixed"):
            state["version"] = version
            launched = request("POST", "/api/evaluations", json={"project_id": project["id"], "dataset_id": dataset["id"], "config_id": cfg["config_id"]})
            active_run = launched["run_id"]
            deadline, last_progress = time.monotonic() + 600, None
            while time.monotonic() < deadline:
                progress = request("GET", f"/api/evaluations/{active_run}/progress")
                signature = (progress["status"], progress["evaluated"], progress["errors"])
                if signature != last_progress:
                    print(json.dumps({"version": version, "run_id": active_run, "progress": progress}, ensure_ascii=False), flush=True)
                    last_progress = signature
                if progress["status"] not in ("pending", "running"):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("bounded live run exceeded 600 seconds")
            report = request("GET", f"/api/evaluations/{active_run}/report")
            listed = request("GET", f"/api/evaluations/{active_run}/results?page_size=100")
            details = [request("GET", f"/api/evaluations/{active_run}/results/{row['id']}") for row in listed["items"]]
            diagnoses = request("GET", f"/api/evaluations/{active_run}/diagnoses?page_size=100")
            run = request("GET", f"/api/evaluations/{active_run}")
            result["runs"][version] = {"run": run, "report": report, "details": details, "diagnoses": diagnoses}
            input_errors = (run.get("error_summary") or {}).get("details", [])
            expected_inputs = 16 if version == "baseline" else 20
            check(f"{version}: all 20 inputs accounted for", listed["total"] == expected_inputs
                  and listed["total"] + len(input_errors) == 20 and run["total_records"] == 20)
            check(f"{version}: every record has both metric rows", all(len(d["metric_results"]) == 2 for d in details))
            check(f"{version}: external calls do not fabricate scores", all(m["score"] is None for d in details for m in d["metric_results"] if m.get("error")))
            metrics = [m for d in details for m in d["metric_results"] if m["name"] == "faithfulness"]
            usable = sum(m["score"] is not None for m in metrics)
            check(f"{version}: real Judge produced usable scores", usable > 0, {"usable": usable, "error_codes": dict(Counter(m["error"]["code"] for m in metrics if m.get("error")))})
            check(f"{version}: every attributed diagnosis has evidence", all(d["evidence"] and d["root_cause"] for d in diagnoses["items"] if d["status"] == "diagnosed"))
            if version == "baseline":
                check("input failures preserve exact adapter error codes",
                      Counter(e["error"]["code"] for e in input_errors) == Counter(expected_errors.values()))
            active_run = None
        first = result["runs"]["baseline"]["run"]["run_id"]
        second = result["runs"]["fixed"]["run"]["run_id"]
        params = {"baseline_run_id": first, "candidate_run_id": second}
        result["comparison"] = request("GET", "/api/comparisons", params=params)
        result["regression"] = request("GET", "/api/comparisons/regression", params=params)
        check("same dataset/config compared DIRECT", result["comparison"]["comparability"]["status"] == "DIRECT")
        after = request("GET", f"/api/datasets/{dataset['id']}/records?page_size=100")
        check("repeated evaluation preserves all golden records", after == golden_before and after["total"] == 20)
        result["rag_requests"] = state["calls"]
        # Keep model scores and deterministic expectations side by side for honest manual review.
        result["manual_review"] = []
        for d in result["runs"]["baseline"]["details"]:
            case = by_question[d["question"]]
            m = next(m for m in d["metric_results"] if m["name"] == "numerical_consistency")
            result["manual_review"].append({"case_id": case["case_id"], "expected": case["expected"],
                                             "actual": m, "diagnoses": d["diagnoses"]})
        save()
    finally:
        if active_run:
            client.post(f"/api/evaluations/{active_run}/cancel")
        server.shutdown()
        server.server_close()
        client.close()
        save()


if __name__ == "__main__":
    main()
