# Level-3 attribution: faithfulness false-negative ("butcher shop")

## Question
Does the platform's faithfulness metric + LLM judge detect a RAGTruth-labelled
"introduced new information" hallucination when it is actually present?

## Controlled experiment (run_faithfulness_bear_attribution_l3.py)

Same question + same contexts, two answer variants:

| variant | answer | faithfulness |
|---|---|---|
| A: bear present | original mistral answer contains "...location named \"bear\"..." | **0.75** |
| B: bear removed | same answer with the two "bear" sentences deleted | **1.0** |

Judge: GLM-5.3-flash via OpenCode Go gateway, temperature 0, timeout 300.

## Conclusion

The GLM judge **can** detect the baseless "bear" content when it is present
(0.75 vs 1.0). So the metric+judge pipeline is NOT blind to this class of
hallucination under this configuration.

## Why the earlier 50-record run showed 1.0 on the same record

Variable differences between the smoke run and this attribution run:

1. **Judge model**: the 50-record run that produced faithfulness=1.0 for
   butcher shop used the runtime GLM config, but the earlier 10-record smoke
   used deepseek-v4-flash. Judge choice materially changes scores.
2. **Context truncation**: the golden dataset deduplicated + truncated contexts
   to 800 chars (metadata.context_trimmed=true). The attribution run uses the
   full 3-passage context.
3. **Answer content**: the golden dataset carried the full original answer;
   the attribution run used the same full answer for variant A, so this
   variable matches.

The honest reading: faithfulness is configuration-sensitive. A single score is
not a stable verdict; Judge model, context length and answer phrasing all move
the number. This reinforces multi-signal evaluation rather than trusting one
score.

## Artifacts
- Script: benchmarks/run_faithfulness_bear_attribution_l3.py
- Output: benchmarks/processed/faithfulness_bear_attribution_l3.json