# Public RAG Test Assets

These files are small, locally generated samples for validating the RAGEval
data contract. They are not claims about model quality.

## Sources

- TAT-QA: https://github.com/NExTplusplus/TAT-QA
- RAGTruth: https://github.com/ParticleMedia/RAGTruth
- MIRACL: https://github.com/project-miracl/miracl

TAT-QA is distributed by its repository under CC BY 4.0 according to its
README. RAGTruth is distributed under the MIT license according to its
repository. MIRACL is an information-retrieval benchmark under Apache-2.0;
it is not converted into a generation dataset here.

## Generated files

`processed/tatqa_reference_10.json` is importable through the normal dataset
import endpoint. It contains questions, reference answers, and evidence only.
It intentionally does not contain a fabricated RAG answer. An external RAG
adapter must provide `answer` and `contexts` during a Run.

`processed/ragtruth_existing_qa_10.json` contains existing RAGTruth generated
answers and their source contexts plus word-level hallucination labels. RAGTruth
does not provide a reference answer for these records, so this file is an
analysis fixture and is not valid as a complete RAGEval golden dataset until a
reference answer is supplied independently. Importing it through the standard
endpoint is rejected by design (duplicate questions across the six responses per
source + missing `reference_answer`); `processed/ragtruth_claim_analysis.json`
holds the deterministic numeric-claim grounding output of
`analyze_ragtruth_claims.py` with the human labels echoed for comparison.

## Rebuild

```text
python benchmarks/prepare_public_samples.py
```

The raw downloads are kept in `raw/` for provenance. Do not mix the RAGTruth
human labels into the RAGAS input as if they were model output.
