"""Repository-owned faithfulness protocol. No SDK imports at module load.

Any instruction/example change requires a new revision. The content hash also
enters metric_version so a prompt edit cannot silently retain comparability.
RAGAS 0.4.3 owns the unchanged JSON schemas and score aggregation.
"""
from __future__ import annotations

import hashlib
import json

REVISION = "faithfulness-claims-v1"

STATEMENT_INSTRUCTION = """Extract every verifiable factual claim from the ANSWER into atomic statements.
Preserve the answer's meaning, wording, subject specificity, negation, numbers, units and qualifiers.
Short answers and fragments are valid statements: copy them without completing an omitted subject.
Do not add a company, person, metric, date, currency or other fact from the question or world knowledge.
The question is not evidence and must not supply extra claims. Preserve pronouns when resolving them
would require information outside the answer. Split compound claims without dropping any claim,
including claims that may be unsupported. Do not convert or repair numeric expressions.
Return JSON with statements; use an empty list only if the answer contains no factual claim."""

STATEMENT_EXAMPLES = [
    ({"question": "公司营收是多少？", "answer": "营收为1亿元"},
     {"statements": ["营收为1亿元"]}),
    ({"question": "甲公司2024年营业收入是多少？", "answer": "100元"},
     {"statements": ["100元"]}),
    ({"question": "公司营收是多少？", "answer": "营收为十亿元，利润为2亿元。"},
     {"statements": ["营收为十亿元", "利润为2亿元"]}),
    ({"question": "乙公司的营收是多少？", "answer": "甲公司营收为1亿元。"},
     {"statements": ["甲公司营收为1亿元。"]}),
    ({"question": "甲公司营收是多少？", "answer": "它的营收为1亿元。"},
     {"statements": ["它的营收为1亿元。"]}),
]

NLI_INSTRUCTION = """Judge each supplied statement using ONLY the retrieved context as evidence.
Return the original statement verbatim, a reason grounded in the context, and verdict 1 or 0.
Use 1 only when the entire claim is supported; otherwise use 0. Do not use the question,
reference answers, world knowledge, or plausible assumptions to supply missing evidence.
An omitted subject in BOTH a short statement and its matching evidence is not an additional claim:
do not demand a company/person name that neither text asserts. Resolve a pronoun or omitted referent
only when the context makes its referent unambiguous. An explicitly asserted entity, metric, time,
currency, negation or qualifier must be supported; never erase it to obtain a match.
Chinese and Arabic numerals and exact unit-scale conversions can express the same quantity
(1亿元 = 10000万元 = 100000000元; 十亿元 = 10亿元). Check the full quantity, metric,
currency and qualifiers, not a shared digit or substring. Different scale, unit, currency, entity,
period, conflicting evidence or an unsupported extra claim require 0. Do not guess ambiguous
numeric conversions. Identical quoted expressions need no numeric conversion to be supported.
For every 0 identify the unsupported part; for every 1 identify the supporting text. Return JSON."""


def _verdict(statement: str, reason: str, verdict: int) -> dict:
    return {"statement": statement, "reason": reason, "verdict": verdict}


NLI_EXAMPLES = [
    ({"context": "营收为1亿元", "statements": ["营收为1亿元", "营收为10000万元", "营收为1万元"]},
     {"statements": [
         _verdict("营收为1亿元", "声明与证据相同，双方都未断言额外主体。", 1),
         _verdict("营收为10000万元", "同一营收：10000万元等于证据中的1亿元。", 1),
         _verdict("营收为1万元", "1万元不等于证据中的1亿元，倍率不同。", 0),
     ]}),
    ({"context": "营收为十亿元", "statements": ["营收为10亿元", "利润为10亿元"]},
     {"statements": [
         _verdict("营收为10亿元", "十亿元与10亿元等价且均为营收。", 1),
         _verdict("利润为10亿元", "证据只包含营收，没有利润。", 0),
     ]}),
    ({"context": "乙公司2023年营收为100美元", "statements": [
        "甲公司2023年营收为100美元", "乙公司2024年营收为100美元", "乙公司2023年营收为100元"]},
     {"statements": [
         _verdict("甲公司2023年营收为100美元", "证据主体为乙公司，不支持甲公司。", 0),
         _verdict("乙公司2024年营收为100美元", "证据年份为2023年，不支持2024年。", 0),
         _verdict("乙公司2023年营收为100元", "证据币种为美元，不能等同于元。", 0),
     ]}),
    ({"context": "关于市场竞争的分析", "statements": ["营收为1亿元"]},
     {"statements": [_verdict("营收为1亿元", "上下文没有营收金额证据。", 0)]}),
]


def prompt_manifest() -> dict:
    """JSON-compatible content; callers may persist it without credentials."""
    # Round-trip gives each caller independent example containers.
    return json.loads(json.dumps({
        "revision": REVISION, "schema_protocol": "ragas-0.4.3/faithfulness",
        "statement": {"instruction": STATEMENT_INSTRUCTION, "examples": STATEMENT_EXAMPLES},
        "nli": {"instruction": NLI_INSTRUCTION, "examples": NLI_EXAMPLES},
    }, ensure_ascii=False))


CONTENT_SHA256 = hashlib.sha256(json.dumps(
    prompt_manifest(), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
).encode("utf-8")).hexdigest()
PROMPT_VERSION = f"{REVISION}:{CONTENT_SHA256}"


def build_prompts():
    """Fresh instances only; never modify RAGAS global prompts or site-packages."""
    from ragas.metrics._faithfulness import (
        NLIStatementInput,
        NLIStatementOutput,
        NLIStatementPrompt,
        StatementGeneratorInput,
        StatementGeneratorOutput,
        StatementGeneratorPrompt,
    )

    statement, nli = StatementGeneratorPrompt(), NLIStatementPrompt()
    statement.instruction = STATEMENT_INSTRUCTION
    statement.examples = [(StatementGeneratorInput(**i), StatementGeneratorOutput(**o))
                          for i, o in STATEMENT_EXAMPLES]
    nli.instruction = NLI_INSTRUCTION
    nli.examples = [(NLIStatementInput(**i), NLIStatementOutput(**o)) for i, o in NLI_EXAMPLES]
    return statement, nli
