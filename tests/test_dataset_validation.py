from __future__ import annotations

from app.datasets import validation
from app.datasets.adapters import to_dataset_records
from app.domain.schemas import DatasetRecordData


def _rec(question="q", **kw):
    return {"question": question, **kw}


# --- duplicate normalization (decided rule: NFKC + trim + collapse + casefold) ---

def test_normalize_question_variants_collapsed() -> None:
    base = validation.normalize_question("  营业　收入 是多少？ ")
    assert base == validation.normalize_question("营业 收入 是多少？")
    assert base == validation.normalize_question("  营业 收入 是多少？".upper())
    assert base == validation.normalize_question("营业　收入　是多少？")
    assert validation.normalize_question("Hello   World") == "hello world"


# --- check 1: schema ---

def test_schema_missing_question_locates_row() -> None:
    records = [_rec(), {"reference_answer": "x"}, _rec()]
    check = validation.check_schema(records)
    assert not check.passed
    assert [i.row_index for i in check.issues] == [1]
    assert check.issues[0].field == "question"


def test_schema_blank_question_and_bad_reference_contexts_type() -> None:
    records = [_rec(question="   "), _rec(reference_contexts="not-a-list")]
    check = validation.check_schema(records)
    assert not check.passed
    assert {(i.row_index, i.field) for i in check.issues} == {(0, "question"), (1, "reference_contexts")}


# --- check 2: duplicate ---

def test_duplicate_strict_fails_and_reports_subsequent_rows() -> None:
    records = [_rec("GDP 2024?"), _rec("其他问题"), _rec("gdp 2024?"), _rec("GDP  2024？")]
    check, drop = validation.check_duplicate(records, policy="strict")
    assert not check.passed
    assert drop == []
    assert [i.row_index for i in check.issues] == [2, 3]


def test_duplicate_skip_passes_and_drops_later_rows() -> None:
    records = [_rec("same"), _rec("other"), _rec("SAME")]
    check, drop = validation.check_duplicate(records, policy="skip")
    assert check.passed
    assert check.count == 1
    assert drop == [2]


# --- check 3: missing_field (registry-driven requirements) ---

def test_missing_field_reports_row_and_field() -> None:
    records = [_rec(reference_answer="ok"), _rec(), _rec(reference_answer=""), _rec()]
    check = validation.check_missing_fields(records, ["reference_answer"])
    assert not check.passed
    assert [(i.row_index, i.field) for i in check.issues] == [(1, "reference_answer"), (2, "reference_answer"), (3, "reference_answer")]


def test_missing_field_supports_dotted_paths() -> None:
    records = [_rec(metadata={"domain": "financial"}), _rec(metadata={})]
    check = validation.check_missing_fields(records, ["metadata.domain"])
    assert not check.passed
    assert [i.row_index for i in check.issues] == [1]


# --- check 4: reference validity (decided sub-rules) ---

def test_reference_blank_answer_empty_list_blank_entry() -> None:
    records = [
        _rec(reference_answer="  "),
        _rec(reference_contexts=[]),
        _rec(reference_contexts=["good", "   "]),
        _rec(),  # absent -> not an issue (fields are individually optional)
    ]
    check = validation.check_reference(records)
    assert not check.passed
    assert [i.row_index for i in check.issues] == [0, 1, 2]


# --- check 5: domain metadata ---

def test_domain_metadata_mismatch_locates_row() -> None:
    records = [_rec(metadata={"domain": "financial"}), _rec(metadata={"domain": "general"}), _rec()]
    check = validation.check_domain_metadata(records, "financial")
    assert not check.passed
    assert [i.row_index for i in check.issues] == [1]


# --- report aggregation (Spec A.4 contract) ---

def test_validate_records_report_shape_and_valid_flag() -> None:
    records = [_rec(reference_answer="ok"), {"question": "x"}, _rec("x")]
    report, drop = validation.validate_records(
        records, domain="general", required_fields=[], duplicate_policy="strict"
    )
    assert not report.valid
    assert drop == []
    names = [c.name for c in report.checks]
    assert names == ["schema", "duplicate", "missing_field", "reference", "domain_metadata"]
    for c in report.checks:
        if not c.passed:
            assert all("row_index" in i.model_dump() for i in c.issues)


def test_validate_records_clean_dataset_passes() -> None:
    records = [_rec(reference_answer="a", reference_contexts=["c"], metadata={"domain": "general"})]
    report, drop = validation.validate_records(
        records, domain="general", required_fields=["reference_answer"], duplicate_policy="strict"
    )
    assert report.valid and drop == []


def test_to_dataset_records_roundtrip() -> None:
    raw = [_rec(reference_answer="a", reference_contexts=["c"], metadata={"k": 1})]
    out = to_dataset_records(raw)
    assert isinstance(out[0], DatasetRecordData)
    assert out[0].reference_contexts == ["c"]
