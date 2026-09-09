"""PDF renderer for the evaluation report export (read-only document layer).

Pure function over the ExportService payload: renders ONLY what is already
persisted (nulls stay "—", no 0-filling, evidence shown as stored). Bilingual
zh+EN on one page, Platypus flowables (automatic pagination), wrapped Paragraph
cells so long evidence content is never truncated.

Font: the built-in Adobe CID font STSong-Light — CJK-capable WITHOUT shipping a
TTF file (Docker slim images have no system fonts; reportlab ships the CID
metrics). Latin glyphs render from the same font, so mixed zh+EN works.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

FONT = "STSong-Light"

_ACCENT = colors.HexColor("#0f766e")
_MUTED = colors.HexColor("#64748b")
_BORDER = colors.HexColor("#cbd5e1")
_HEAD_BG = colors.HexColor("#f1f5f9")

pdfmetrics.registerFont(UnicodeCIDFont(FONT))


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName=FONT, wordWrap="CJK", splitLongWords=True)
    return {
        "title": ParagraphStyle("t", fontSize=17, leading=22, **base),
        "sub": ParagraphStyle("s", fontSize=9, leading=12, textColor=_MUTED, **base),
        "h1": ParagraphStyle("h1", fontSize=13, leading=17, spaceBefore=14,
                             spaceAfter=6, textColor=_ACCENT, **base),
        "body": ParagraphStyle("b", fontSize=9, leading=13, **base),
        "small": ParagraphStyle("sm", fontSize=8, leading=11, **base),
        "note": ParagraphStyle("n", fontSize=8, leading=11, textColor=_MUTED, **base),
        "cell": ParagraphStyle("c", fontSize=8, leading=11, **base),
    }


def _s(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def _ts(v: Any) -> str:
    return v.isoformat(sep=" ", timespec="seconds") if isinstance(v, datetime) else _s(v)


class _Doc(BaseDocTemplate):
    """A4 with page numbers in the footer."""

    def __init__(self, buf: io.BytesIO, payload: dict) -> None:
        super().__init__(buf, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm,
                         leftMargin=14 * mm, rightMargin=14 * mm,
                         title=f"RAGEval Evaluation Report {payload['run']['run_id'][:8]}")
        self._payload = payload
        frame = self._frame(self.width, self.height)
        self.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=self._footer)])

    @staticmethod
    def _frame(w: float, h: float):
        from reportlab.platypus import Frame
        return Frame(14 * mm, 16 * mm, w, h)

    def _footer(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(_MUTED)
        stamp = _ts(self._payload.get("generated_at"))
        canvas.drawString(14 * mm, 9 * mm, f"RAGEval Studio — report {stamp} — 只读导出 read-only")
        canvas.drawRightString(14 * mm + doc.width, 9 * mm, f"第 {doc.page} 页 / Page {doc.page}")
        canvas.restoreState()


def _kv_table(rows: list[tuple[str, Any]], st) -> Table:
    data = [[Paragraph(k, st["small"]), Paragraph(_s(v), st["cell"])] for k, v in rows]
    t = Table(data, colWidths=[52 * mm, None])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), _HEAD_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _grid(header: list[str], rows: list[list[Any]], st, widths: list[float] | None = None) -> Table:
    data = [[Paragraph(h, st["small"]) for h in header]] + [
        [Paragraph(_s(c), st["cell"]) for c in row] for row in rows
    ]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _basis_lines(basis: dict | None) -> list[str]:
    """Persisted comparison_basis → display lines (values as stored)."""
    if not basis:
        return []
    out: list[str] = []

    def side(name: str) -> None:
        s = basis.get(name) or {}
        prim = s.get("primary") or {}
        cands = s.get("candidates") or []
        if prim:
            out.append(f"{name}: {_s(prim.get('raw'))} value={_s(prim.get('value'))} "
                       f"unit={_s(prim.get('unit'))} (status={_s(s.get('side_status'))}, "
                       f"candidates={len(cands)})")

    side("reference")
    side("answer")
    out.append(f"comparison_type: {_s(basis.get('comparison_type'))} "
               f"| method: {_s(basis.get('method'))}")
    diff = basis.get("diff") or {}
    if isinstance(diff, dict):
        base = diff.get("base")
        if isinstance(base, dict):
            out.append(f"numeric difference: ref={_s(base.get('reference'))} "
                       f"ans={_s(base.get('answer'))} abs_diff={_s(base.get('abs_diff'))}")
        for k, v in diff.items():
            if k in ("base",):
                continue
            out.append(f"{k}: {_s(v)}")
    return out


def render_report_pdf(payload: dict) -> bytes:
    st = _styles()
    run = payload["run"]
    dims = payload["quality_dimensions"]
    story: list[Any] = []

    # ---- header ----
    story.append(Paragraph("评估报告 Evaluation Report — RAGEval Studio", st["title"]))
    banner = (f"{run['dataset_name']} / {run['dataset_version']} · run {run['run_id']}"
              f" · 状态 status: {run['status']}"
              + ("" if run["is_final"] else " — 运行未终结 NOT FINAL，本报告非终态"))
    story.append(Paragraph(banner, st["sub"]))
    story.append(Spacer(0, 4))

    # ---- 1. Executive summary ----
    summary = payload["summary"]
    weak = summary.get("weakest_dimension")
    story.append(Paragraph("1. 总结 Executive Summary", st["h1"]))
    story.append(_kv_table([
        ("数据集 Dataset", f"{run['dataset_name']} ({run['dataset_id']})"),
        ("数据版本 Dataset Version", run["dataset_version"]),
        ("Run ID", run["run_id"]),
        ("评估时间 Evaluation Time",
         f"{_ts(run['created_at'])} → {_ts(run['finished_at'])}"),
        ("总体分数 Overall Score", summary.get("overall_score")),
        ("最弱质量维度 Weakest Dimension",
         f"{weak['dimension']} ({_s(weak['score'])})" if weak else None),
        ("评估总览 Overall Assessment", None),
    ] + [("", "· " + line) for line in summary.get("assessment_lines") or []], st))

    # ---- 2. Quality dimensions ----
    story.append(Paragraph("2. 质量维度 Quality Dimensions"
                           "（缺失分数显示 “—”，不以 0 填充 no zero-filling）", st["h1"]))
    story.append(_grid(
        ["维度 Dimension", "分数 Score", "失败 Failures", "无法判定 Undetermined",
         "诊断覆盖 Diag.Cov.", "证据覆盖 Evid.Cov.", "有效行/总行 Rows"],
        [[d["dimension"], d["score"], d["failure_count"], d["undetermined_count"],
          d["diagnosis_coverage"], d["evidence_coverage"],
          f"{d.get('evaluated_rows')} / {d.get('total_rows')}"] for d in dims],
        st,
    ))

    # ---- 3. Metrics ----
    story.append(Paragraph("3. 指标 Metrics（本次 Run 实际执行 only）", st["h1"]))
    story.append(_grid(
        ["指标 Metric", "分数 Score", "阈值 Threshold", "状态 Status",
         "有效 Valid", "无效 Invalid", "错误 Errors"],
        [[m["name"], m["score"], m["threshold"], m["status"], m["valid_count"],
          m["invalid_count"], m["error_count"]] for m in payload["metrics"]],
        st,
    ))
    story.append(Paragraph(
        f"启用指标 enabled metrics: {_s(payload.get('enabled_metrics') or None)}；"
        "注册但未启用的指标不出现在本节（registered-but-not-enabled are never shown as executed）。",
        st["note"]))

    # ---- execution errors (kept separate from quality, honesty contract) ----
    errs = payload.get("execution_errors") or []
    if errs:
        story.append(Paragraph("3b. 执行错误 Execution Errors"
                               "（评估执行问题，非质量结论 — not quality judgments）", st["h1"]))
        story.append(_grid(
            ["记录 Record", "指标 Metric", "错误码 Code", "信息 Message"],
            [[e.get("record_id"), e.get("metric"), e.get("error_code"), e.get("message")]
             for e in errs],
            st,
        ))

    # ---- 4. Failure analysis ----
    failures = payload["failures"]
    story.append(Paragraph(f"4. 失败分析 Failure Analysis（{len(failures)} 条）", st["h1"]))
    if not failures:
        story.append(Paragraph("本 Run 无确认失败 no confirmed failures。", st["body"]))
    for i, f in enumerate(failures, 1):
        head = (f"<b>#{i} {f['failure_type']}</b> · {_s(f['severity'])} · "
                f"metric {_s(f['related_metric'])} · record {_s(f['record_id'])}")
        story.append(Paragraph(head, st["body"]))
        if f.get("question"):
            story.append(Paragraph(f"问题 Question: {f['question']}", st["note"]))
        if f.get("root_cause"):
            story.append(Paragraph(f"根因 Root cause: {f['root_cause']}", st["small"]))
        for line in _basis_lines(f.get("comparison_basis")):
            story.append(Paragraph(f"· {line}", st["small"]))
        items = f.get("evidence") or []
        if items:
            story.append(_grid(
                ["证据类型 Type", "来源 Source", "定位 Locator", "内容 Content"],
                [[it.get("type"), it.get("source"), it.get("locator"), it.get("content")]
                 for it in items],
                st, [26 * mm, 24 * mm, 32 * mm, None],
            ))
        else:
            story.append(Paragraph("（无证据条目 no evidence items — 保持原样，不补造）", st["note"]))
        story.append(Spacer(0, 6))

    # ---- 5. Diagnoses ----
    story.append(Paragraph("5. 诊断 Diagnoses（既有 taxonomy，无新增规则 existing taxonomy only）", st["h1"]))
    drows = [d for d in payload["diagnoses"] if d["status"] == "diagnosed"]
    story.append(_grid(
        ["诊断 Diagnosis (root cause)", "代码 Code", "状态 Status", "严重度 Severity",
         "证据 Evidence", "关联指标 Related Metric"],
        [[d.get("root_cause"), d.get("failure_type"), d.get("status"), d.get("severity"),
          ("可用 available ×" + str(d.get("evidence_count", 0)))
          if d.get("evidence_available") else "不可用 unavailable",
          d.get("related_metric")] for d in drows],
        st,
    ))

    # ---- 6. Undetermined ----
    und = payload["undetermined"]
    su = summary["undetermined"]
    story.append(Paragraph(
        f"6. 无法判定 Undetermined（{su['count']} 条，"
        f"占全部指标行 {su['ratio_of_metric_rows'] if su['ratio_of_metric_rows'] is not None else '—'}）",
        st["h1"]))
    story.append(Paragraph(su["explanation"], st["body"]))
    if su["top_reasons"]:
        story.append(_grid(["原因分类 Reason", "数量 Count"],
                           [[r["reason"], r["count"]] for r in su["top_reasons"]],
                           st, [110 * mm, None]))
    samples = und[:10]
    if samples:
        story.append(Paragraph("代表样本 representative samples（≤10，展示用 not exhaustive）", st["note"]))
        story.append(_grid(["记录 Record", "指标 Metric", "原因 Reason", "缺失证据 Missing"],
                           [[smp.get("record_id"), smp.get("related_metric"), smp.get("reason"),
                             _s(smp.get("missing_evidence") or None)] for smp in samples],
                           st))

    # ---- 7. Recommendations ----
    recs = payload["recommendations"]
    story.append(Paragraph(f"7. 改进建议 Recommendations（{len(recs)} 条，全部来自持久化系统输出 "
                           "— nothing invented）", st["h1"]))
    story.append(_grid(
        ["建议 Action", "优先级 P", "来源 Source", "关联诊断 Diagnosis", "影响记录 Record"],
        [[r["action"], r["priority"], r["source"], r.get("related_failure_type"),
          r.get("affected_record_id")] for r in recs],
        st,
    ))

    # ---- 8. Summary recommendations ----
    story.append(Paragraph("8. 总结建议 Summary Recommendations（规则化生成，无 LLM "
                           "rule-based from persisted results only）", st["h1"]))
    sr_rows: list[tuple[str, Any]] = []
    if summary.get("most_failed_metric"):
        m = summary["most_failed_metric"]
        sr_rows.append(("失败最集中 Metric", f"{m['metric']}（{m['count']} 条）"))
    if summary.get("most_frequent_diagnosis"):
        d = summary["most_frequent_diagnosis"]
        sr_rows.append(("最频繁诊断 Diagnosis", f"{d['failure_type']}（{d['count']} 条）"))
    if summary.get("highest_impact_recommendation"):
        h = summary["highest_impact_recommendation"]
        sr_rows.append(("影响范围最大的建议 Top Recommendation",
                        f"{h['action']}（受影响记录 {h['affected_records']} 条）"))
    if summary.get("most_failed_metric") is None:
        sr_rows.append(("失败最集中 Metric", "无确认失败 none"))
    story.append(_kv_table(sr_rows, st))

    # ---- 9. Reproducibility ----
    rp = payload["reproducibility"]
    story.append(Paragraph("9. 可复现信息 Reproducibility", st["h1"]))
    story.append(_kv_table([
        ("Dataset ID", run["dataset_id"]),
        ("Dataset Version", rp["dataset_version"]),
        ("Run ID", run["run_id"]),
        ("Profile", rp["profile"]),
        ("Config Version", rp["config_version"]),
        ("Metric Version", rp["metric_version"]),
        ("Judge Provider", run.get("judge_provider") or rp.get("judge_provider") or None),
        ("Judge Model", rp["judge_model"]),
        ("Judge Model Version", rp["judge_model_version"]),
        ("RAG Input Mode", rp["rag_input"]),
        ("Evaluation Timestamp", rp["timestamp"]),
        ("Report Generated", _ts(payload.get("generated_at"))),
    ], st))
    story.append(Paragraph(
        "本导出为只读快照 read-only export — 不重跑 Engine/Judge/RAG/Diagnosis。"
        "凭据 secrets 永不包含在本文件中。", st["note"]))

    buf = io.BytesIO()
    doc = _Doc(buf, payload)
    doc.build(story)
    return buf.getvalue()


def export_filename(run_id: str, suffix: str, when: datetime | None = None) -> str:
    stamp = (when or datetime.now(UTC)).strftime("%Y%m%d")
    return f"rageval_report_{run_id[:8]}_{stamp}.{suffix}"
