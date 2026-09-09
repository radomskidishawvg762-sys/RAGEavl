# False-Negative Case Study: "butcher shop phone number"

## Setup

- Dataset: RAGTruth QA, source 14292
- Generated answer model: mistral-7B-instruct
- Human label: **Evident Baseless Info** (span "none of them are for a specific
  location named 'bear'...", meta: HIGH INTRO OF NEW INFO)
- Judge under test: GLM-5.3-flash via OpenCode Go gateway
- **Faithfulness score: 1.0** (full credit)

## Why this is a false negative

The generated answer introduces content about a location named "bear" that
appears nowhere in the retrieved passages. RAGTruth annotators flagged it as an
evident baseless (unsupported) span.

The platform, however, gave the answer a perfect faithfulness score of 1.0:

1. **RAGAS faithfulness = 1.0** — the claim-level judge did not mark the
   "bear" statement as unsupported.
2. **Deterministic numeric-claim check returns `[]`** — the baseless span
   contains no numeric values, so the platform's deterministic numeric
   grounding path (used as evidence for `generation.unsupported_claim`) finds
   nothing to compare.
3. No other enabled metric was run in this smoke (only faithfulness was
   enabled via metric_overrides), so no cross-signal surfaced the issue.

## Implication for the product

A single `faithfulness` score is **insufficient** to catch RAGTruth-style
hallucinations (especially "introduced new information" spans that contain no
numeric facts). This is an empirical demonstration that:

- Multi-metric evaluation is necessary (faithfulness + answer_relevancy +
  integrity + retrieval metrics together).
- Evidence-chain and human-in-the-loop review remain essential for
  non-numeric hallucination detection.
- The platform should surface "high faithfulness but suspicious signal"
  cross-checks rather than reporting one score as a verdict.

## What would catch it

- A claim-level human-in-the-loop review of low-confidence spans.
- An additional "context-support audit" that lists answer claims not found in
  retrieved contexts (beyond numeric ones) — a natural P1 extension using the
  existing evidence contract, with LLM fallback for non-numeric claims.
- Cross-metric comparison: answer_relevancy / retrieval overlap could hint the
  answer drifted from the retrieved evidence.

## Artifacts

- Golden record: `benchmarks/processed/ragtruth_golden_qa_50.json` (source_id 14292)
- Faithfulness smoke run: `e6b07079` (deepseek) and 50-record run `b8759669` (GLM)
- Analysis: `benchmarks/processed/ragtruth_faithfulness_50_gold_align.json`