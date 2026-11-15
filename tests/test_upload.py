import io

import pypdf
import pytest

from app import extraction
from app.extraction import accept_fields


def make_pdf(pages: int = 1) -> bytes:
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def field(value, source=None, page=1, note=None):
    return {"value": value, "source_text": source if source is not None else value, "page": page, "note": note}


GOOD_RAW = {
    "patient_first_name": field("Jane", "Patient: DOE, JANE"),
    "patient_last_name": field("Doe", "Patient: DOE, JANE"),
    "patient_dob": field("1950-04-12", "DOB: 04/12/1950"),
    "provider_name": field("Dr. John Smith, MD", "Ordering Physician: Dr. John Smith, MD"),
    "provider_npi": field("1234567893", "NPI: 1234 567 893"),
}


@pytest.fixture()
def fake_model(monkeypatch):
    """Replace the Claude call with a canned result; returns a setter for that result."""
    result = {"raw": GOOD_RAW}
    monkeypatch.setattr(extraction, "call_model", lambda pdf: result["raw"])
    return lambda raw: result.update(raw=raw)


def upload(client, headers, data: bytes, name="order.pdf"):
    return client.post("/orders/upload", files={"file": (name, data, "application/pdf")}, headers=headers)


# ---- endpoint ----

def test_upload_creates_complete_order(client, auth_headers, fake_model):
    h = auth_headers()
    r = upload(client, h, make_pdf(3), name="../../etc/fax.pdf")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "complete"
    assert body["source"] == "pdf_upload"
    assert body["source_filename"] == "fax.pdf"
    assert body["patient_dob"] == "1950-04-12"
    assert body["provider_npi"] == "1234567893"
    assert body["validation_issues"] == []
    # Uploaded orders belong to the uploader only.
    assert client.get(f"/orders/{body['id']}", headers=auth_headers("other@example.com")).status_code == 404


def test_missing_field_is_left_null_and_flagged_then_fixable(client, auth_headers, fake_model):
    fake_model({**GOOD_RAW, "provider_npi": field(None, None, None, "NPI field is blank on the form")})
    h = auth_headers()
    body = upload(client, h, make_pdf()).json()
    assert body["provider_npi"] is None
    assert body["status"] == "needs_review"
    assert body["validation_issues"] == [
        {"field": "provider_npi", "message": "not found in document: NPI field is blank on the form",
         "rejected_value": None}
    ]
    r = client.patch(f"/orders/{body['id']}", json={"provider_npi": "1234567893"}, headers=h)
    assert r.json()["status"] == "complete"
    assert r.json()["validation_issues"] == []


def test_upload_rejects_non_pdf_and_requires_auth(client, auth_headers, fake_model):
    h = auth_headers()
    assert upload(client, h, b"hello, not a pdf", name="x.pdf").status_code == 415
    assert upload(client, h, b"%PDF-1.7 garbage").status_code == 422
    assert upload(client, {}, make_pdf()).status_code == 401


def test_upload_size_limit(client, auth_headers, fake_model, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    assert upload(client, auth_headers(), make_pdf()).status_code == 413


def test_upload_without_api_key_returns_503(client, auth_headers):
    # No fake_model fixture: the real call_model runs and must refuse without a key.
    assert upload(client, auth_headers(), make_pdf()).status_code == 503


def test_extraction_errors_are_surfaced(client, auth_headers, monkeypatch):
    def boom(pdf):
        raise extraction.ExtractionError(502, "Extraction service error")

    monkeypatch.setattr(extraction, "call_model", boom)
    r = upload(client, auth_headers(), make_pdf())
    assert r.status_code == 502
    assert client.get("/orders", headers=auth_headers("x@example.com")).json() == []


# ---- deterministic acceptance checks ----

def test_accept_fields_all_good():
    fields, issues = accept_fields(GOOD_RAW)
    assert issues == []
    assert str(fields["patient_dob"]) == "1950-04-12"
    assert fields["provider_npi"] == "1234567893"


@pytest.mark.parametrize(
    "name,item,expected",
    [
        ("provider_npi", field("1234567890", "NPI 1234567890"), "check digit"),
        ("provider_npi", field("1234567893", "NPI 1234567"), "not supported"),  # invented digits
        ("patient_dob", field("2099-01-01", "DOB 01/01/2099"), "future"),
        ("patient_dob", field("1950-04-12", "DOB 04/12/50"), "four-digit"),  # century would be a guess
        ("patient_dob", field("12 April 1950", "DOB 12 April 1950"), "YYYY-MM-DD"),
        ("patient_first_name", field("Jonathan", "Patient: Jon Smith"), "not supported"),  # completed name
        ("patient_last_name", field("Sm1th", "Patient: Sm1th"), "not allowed"),
        ("provider_name", field("Dr. Who", None), None),
        ("patient_first_name", field("Jane", ""), "no supporting source"),
        ("patient_last_name", field(None, None, None, None), "not found in document"),
        ("patient_last_name", field("   ", None), "not found in document"),
    ],
)
def test_accept_fields_rejects_unsupported_values(name, item, expected):
    fields, issues = accept_fields({**GOOD_RAW, name: item})
    if expected is None:  # source defaults to the value itself
        assert fields[name] is not None
        return
    assert fields[name] is None
    assert len(issues) == 1 and issues[0]["field"] == name
    assert expected in issues[0]["message"]


def test_invalid_npi_from_document_is_flagged_with_rejected_value(client, auth_headers, fake_model):
    # Mirrors the real sample fax: the printed NPI fails the CMS check digit.
    fake_model({**GOOD_RAW, "provider_npi": field("9182734556", "Prescriber NPI: 9182734556")})
    body = upload(client, auth_headers(), make_pdf(4)).json()
    assert body["provider_npi"] is None
    assert body["status"] == "needs_review"
    [issue] = body["validation_issues"]
    assert issue["field"] == "provider_npi"
    assert issue["rejected_value"] == "9182734556"
    assert "check digit" in issue["message"]


def test_output_schema_within_api_union_limit():
    # The API rejects structured-output schemas with more than 16 union-typed (anyOf / type array) parameters.
    def count_unions(node) -> int:
        if isinstance(node, dict):
            own = 1 if ("anyOf" in node or isinstance(node.get("type"), list)) else 0
            return own + sum(count_unions(v) for v in node.values())
        if isinstance(node, list):
            return sum(count_unions(v) for v in node)
        return 0

    assert count_unions(extraction.OUTPUT_SCHEMA) <= 16


def test_empty_string_sentinels_from_model_are_treated_as_missing():
    raw = {**GOOD_RAW, "provider_name": {"value": None, "source_text": "", "page": 0, "note": ""}}
    fields, issues = accept_fields(raw)
    assert fields["provider_name"] is None
    assert issues == [{"field": "provider_name", "message": "not found in document"}]


def test_accept_fields_handles_missing_keys():
    fields, issues = accept_fields({})
    assert all(v is None for v in fields.values())
    assert len(issues) == 5
