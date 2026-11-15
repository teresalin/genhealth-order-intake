"""Offline regression tests over real model outputs recorded from synthetic test documents.

Each case is the exact JSON claude-opus-5-5 returned for one synthetic DME form (fictional patient and
provider; NPIs checked unassigned in NPPES). These tests re-run only the deterministic acceptance checks,
so they cost nothing; they do not prove what the model will return next time.
"""
import json
from pathlib import Path

import pytest

from app.extraction import accept_fields

CASES = json.loads((Path(__file__).parent / "fixtures" / "recorded_extractions.json").read_text("utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["document"] for c in CASES])
def test_recorded_extraction_outcome(case):
    fields, issues = accept_fields(case["raw"], page_count=case["page_count"])
    assert {k: (str(v) if v is not None else None) for k, v in fields.items()} == case["expected_fields"]
    assert sorted(i["field"] for i in issues) == sorted(case["expected_issue_fields"])


def test_conflicts_are_reported_as_not_extracted_not_as_missing():
    case = next(c for c in CASES if c["document"].startswith("S3_"))
    _, issues = accept_fields(case["raw"], page_count=case["page_count"])
    [dob_issue] = issues
    assert dob_issue["message"].startswith("not extracted:")
    assert "conflict" in dob_issue["message"].lower()
