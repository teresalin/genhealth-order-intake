"""Offline tests for the deterministic DOB and page-reference checks in accept_fields."""
import pytest

from app.extraction import accept_fields
from tests.test_upload import GOOD_RAW, field


def check(name: str, item: dict, page_count: int = 1):
    fields, issues = accept_fields({**GOOD_RAW, name: item}, page_count=page_count)
    mine = [i for i in issues if i["field"] == name]
    return fields[name], (mine[0]["message"] if mine else None)


# ---- DOB: the whole date must be supported by the quote ----

@pytest.mark.parametrize(
    "source",
    [
        "DOB: 04/12/1950",
        "DOB: 4/12/1950",
        "DOB: 04 / 12 / 1950",          # split form boxes
        "DOB: 04 /12 /1950",
        "DOB: 04-12-1950",
        "DOB: 04.12.1950",
        "Date of Birth 1950-04-12",
        "Birth Date: April 12, 1950",
        "D.O.B. 12 Apr 1950",
        "Patient DOB: 04/12/1950   Order Date: 03/01/2026   Signed 03/02/2026",  # other dates ignored
        "Name: Jane Doe DOB (mm/dd/yyyy): 04/12/1950",
    ],
)
def test_dob_accepted_when_whole_date_matches(source):
    value, problem = check("patient_dob", field("1950-04-12", source))
    assert problem is None, problem
    assert str(value) == "1950-04-12"


@pytest.mark.parametrize(
    "source,expected",
    [
        ("DOB: 05/12/1950", "does not match"),        # wrong month (old check only compared year and day)
        ("DOB: 04/13/1950", "does not match"),        # wrong day
        ("DOB: 04/12/1951", "does not match"),        # wrong year
        ("DOB: 12/04/1950", "does not match"),        # day/month swapped (US order is month/day)
        ("DOB: 04/12/50", "four-digit"),              # two-digit year: century never inferred
        ("DOB: 04 / 12 / 50", "four-digit"),
        ("DOB: 04/12/1950  Date of Birth: 04/21/1950", "conflicting"),
        ("Signed 03/01/2026, seen 04/12/1950", "none is labeled"),  # several unlabeled dates
        ("DOB: ________  Order Date: 04/12/1950", "no date follows"),  # blank DOB box, other date nearby
        ("DOB: April 1950", "no date follows"),       # incomplete date
        ("Patient: Jane Doe", "no date found"),
    ],
)
def test_dob_rejected_when_not_fully_supported(source, expected):
    value, problem = check("patient_dob", field("1950-04-12", source))
    assert value is None
    assert expected in problem


def test_dob_single_unlabeled_date_must_still_match():
    assert check("patient_dob", field("1950-04-12", "04/12/1950"))[1] is None
    assert "does not match" in check("patient_dob", field("1950-04-12", "04/11/1950"))[1]


def test_dob_future_and_minimum_checks_still_apply():
    assert "future" in check("patient_dob", field("2999-01-01", "DOB: 01/01/2999"))[1]
    assert "before" in check("patient_dob", field("1899-12-31", "DOB: 12/31/1899"))[1]


def test_dob_rejected_value_is_reported():
    fields, issues = accept_fields({**GOOD_RAW, "patient_dob": field("1950-04-12", "DOB: 05/12/1950")}, page_count=1)
    assert fields["patient_dob"] is None
    assert issues == [{"field": "patient_dob", "rejected_value": "1950-04-12",
                       "message": "extracted value does not match the date in the quoted source text"}]


# ---- page references ----

@pytest.mark.parametrize("page,page_count", [(1, 1), (3, 3), (2, 4)])
def test_valid_page_reference_accepted(page, page_count):
    value, problem = check("provider_npi", field("1234567893", "NPI: 1234567893", page=page), page_count)
    assert problem is None
    assert value == "1234567893"


@pytest.mark.parametrize("page", [0, -1, 4, 99, None, "1", 1.0, True])
def test_invalid_page_reference_rejected(page):
    value, problem = check("provider_npi", field("1234567893", "NPI: 1234567893", page=page), page_count=3)
    assert value is None
    assert "is not a page of this document (1-3)" in problem


def test_page_zero_allowed_only_for_unextracted_field():
    fields, issues = accept_fields(
        {**GOOD_RAW, "provider_npi": {"value": None, "source_text": "", "page": 0, "note": ""}}, page_count=2)
    assert fields["provider_npi"] is None
    assert issues == [{"field": "provider_npi", "message": "not found in document"}]


def test_page_check_applies_to_every_field():
    raw = {k: {**v, "page": 5} for k, v in GOOD_RAW.items()}
    fields, issues = accept_fields(raw, page_count=4)
    assert all(v is None for v in fields.values())
    assert len(issues) == 5 and all("not a page of this document" in i["message"] for i in issues)
