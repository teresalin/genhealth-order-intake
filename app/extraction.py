"""PDF -> order fields: Claude reads the document, then deterministic checks decide what is accepted.

The model is asked to quote the text each value came from and to return null rather than infer.
Every value is then re-checked here; anything missing, malformed, or unsupported by its quote is
left empty on the order and recorded as a validation issue, so nothing guessed is ever saved.
"""
import base64
import json
import logging
import re
from datetime import date
from functools import lru_cache

import anthropic

from app.config import get_settings
from app.models import REQUIRED_ORDER_FIELDS
from app.validation import dob_error, name_error, normalize_name, npi_error


log = logging.getLogger(__name__)


class ExtractionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


SYSTEM_PROMPT = """You extract data from durable medical equipment (DME) orders received by fax or upload.
Documents may be scanned, rotated, low quality, handwritten in places, and span many pages with cover sheets,
clinical notes, and insurance forms. Read every page.

Extract exactly these fields:
- patient_first_name, patient_last_name: the patient the equipment is ordered for.
- patient_dob: the patient's date of birth, returned as YYYY-MM-DD. Numeric dates in these US documents are
  month/day/year.
- provider_name: the ordering (prescribing) provider who signed or issued the order. Not the DME supplier,
  not the fax sender's office staff, not a referring provider if a different ordering provider is named.
  Return the person's name as written (titles/credentials such as "Dr." or "MD" may be included).
- provider_npi: the ordering provider's 10-digit National Provider Identifier.

Rules. A wrong value can deny a patient's claim, so accuracy matters more than completeness:
- Only report a value that is explicitly written in the document. Never infer, guess, complete partial text,
  or fill in from context.
- If a field is absent, illegible, cut off, or you are not sure which of several candidates is correct,
  set value to null and explain briefly in note.
- source_text must quote, verbatim, the text the value was read from (including its label when present),
  and page is the 1-based page it appears on. When value is null, use "" for source_text and 0 for page.
- note is "" unless there is something to explain.
- Do not swap first and last names: "DOE, JANE" means last name DOE, first name JANE."""


def _field_schema(description: str) -> dict:
    # Only `value` is nullable: the API caps structured-output schemas at 16 union-typed parameters,
    # so the other parts use "" / 0 for "none".
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "source_text": {"type": "string"},
            "page": {"type": "integer"},
            "note": {"type": "string"},
        },
        "required": ["value", "source_text", "page", "note"],
        "additionalProperties": False,
    }


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_first_name": _field_schema("Patient first name"),
        "patient_last_name": _field_schema("Patient last name"),
        "patient_dob": _field_schema("Patient date of birth as YYYY-MM-DD"),
        "provider_name": _field_schema("Ordering provider full name"),
        "provider_npi": _field_schema("Ordering provider 10-digit NPI"),
    },
    "required": list(REQUIRED_ORDER_FIELDS),
    "additionalProperties": False,
}


@lru_cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key, timeout=180.0, max_retries=2)


def call_model(pdf_bytes: bytes) -> dict:
    """Send the PDF to Claude and return the raw structured result. Raises ExtractionError."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ExtractionError(503, "PDF extraction is not configured on this server")
    try:
        response = _client().beta.messages.create(
            model=settings.anthropic_model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(pdf_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": "Extract the order fields from this document."},
                ],
            }],
        )
    # Provider error details stay in server logs (no document content is logged); clients get a generic message.
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        log.error("Extraction auth/permission error (request_id=%s): %s", e.request_id, e.message)
        raise ExtractionError(503, "PDF extraction is not configured correctly on this server")
    except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
        log.warning("Extraction temporarily unavailable: %s", type(e).__name__)
        raise ExtractionError(503, "Extraction service temporarily unavailable; retry later")
    except anthropic.APIStatusError as e:
        # Includes 400s such as account billing problems; uploads are pre-validated, so a 400 is not the user's fault.
        log.error("Extraction API error %s (request_id=%s): %s", e.status_code, e.request_id, e.message)
        raise ExtractionError(502, "Extraction service error")

    u = response.usage
    log.info(
        "Extraction call: model=%s stop_reason=%s input_tokens=%s output_tokens=%s request_id=%s",
        response.model, response.stop_reason, u.input_tokens, u.output_tokens, response._request_id,
    )
    if response.stop_reason == "refusal":
        raise ExtractionError(422, "The document could not be processed")
    if response.stop_reason == "max_tokens":
        raise ExtractionError(502, "Extraction output was truncated")
    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        return json.loads(text or "")
    except json.JSONDecodeError:
        raise ExtractionError(502, "Extraction returned malformed output")


# ---- deterministic acceptance checks ----

def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _check_name(value: str, source: str) -> tuple[str | None, str | None]:
    value = normalize_name(value)
    if err := name_error(value):
        return None, f"extracted value {err}"
    # Each word of the name must appear as a whole word in the quoted source
    # (guards against invented or auto-completed names).
    src = _norm(source)
    for part in value.split():
        part = part.strip(".,").casefold()
        if part and not re.search(rf"(?<!\w){re.escape(part)}(?!\w)", src):
            return None, "extracted value is not supported by the quoted source text"
    return value, None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_SEP = r"\s*[/.\-]\s*"
# (pattern, order of the captured groups). Numeric dates in US documents are month/day/year.
_DATE_PATTERNS = [
    (re.compile(rf"(?<!\d)(\d{{4}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})(?!\d)"), "ymd"),          # 1990-02-02
    (re.compile(rf"(?<!\d)(\d{{1,2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{2,4}})(?!\d)"), "mdy"),        # 02 / 02 / 1990
    (re.compile(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{2,4}})(?!\d)", re.I), "Mdy"),  # Feb 2, 1990
    (re.compile(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH_RE},?\s+(\d{{2,4}})(?!\d)", re.I), "dMy"),  # 2 Feb 1990
]
_DOB_LABEL = re.compile(r"\bdob\b|\bd\.\s*o\.\s*b\b|\bdate\s+of\s+birth\b|\bbirth\s*date\b", re.I)
# Text allowed between a DOB label and its date: punctuation, spaces, blank-line underscores and an
# optional format hint such as "(mm/dd/yyyy)". Any other word (e.g. another field's label) breaks the link.
_FORMAT_HINT = re.compile(r"\(?\s*[mdy]{1,4}\s*[/.\-]\s*[mdy]{1,4}\s*[/.\-]\s*[mdy]{2,4}\s*\)?", re.I)
_LABEL_GAP_OK = re.compile(r"[\s:._\-()#]*")


def _date_candidates(source: str) -> list[tuple[int, str, int, int]]:
    """All dates written in `source` as (position, year_text, month, day), left to right."""
    found = []
    masked = source
    for pattern, order in _DATE_PATTERNS:
        for m in pattern.finditer(masked):
            g = m.groups()
            if order == "ymd":
                year, month, day = g[0], int(g[1]), int(g[2])
            elif order == "mdy":
                month, day, year = int(g[0]), int(g[1]), g[2]
            elif order == "Mdy":
                month, day, year = _MONTHS[g[0].lower()[:3]], int(g[1]), g[2]
            else:
                day, month, year = int(g[0]), _MONTHS[g[1].lower()[:3]], g[2]
            found.append((m.start(), year, month, day))
        # Blank out matches so a later, looser pattern cannot re-read part of the same date.
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    return sorted(found)


def _check_dob(value: str, source: str) -> tuple[date | None, str | None]:
    try:
        dob = date.fromisoformat(value.strip())
    except ValueError:
        return None, "extracted value is not a valid YYYY-MM-DD date"
    if err := dob_error(dob):
        return None, f"date of birth {err}"

    candidates = _date_candidates(source)
    # When the quote labels a date of birth, only a date written right after a label counts; other dates
    # in the quote (order, signature, visit dates) are not candidates. A blank DOB field followed by some
    # other date ("DOB: ____  Date: 03/01/2026") therefore yields no candidate rather than the wrong date.
    labels = [m.end() for m in _DOB_LABEL.finditer(source)]
    if labels:
        labeled = []
        for end in labels:
            after = [c for c in candidates if c[0] >= end]
            if not after:
                continue
            gap = _FORMAT_HINT.sub("", source[end:after[0][0]])
            if _LABEL_GAP_OK.fullmatch(gap) and after[0] not in labeled:
                labeled.append(after[0])
        if not labeled:
            return None, "no date follows the date-of-birth label in the quoted source text"
        candidates = labeled
    if not candidates:
        return None, "no date found in the quoted source text"
    if any(len(year) != 4 for _, year, _, _ in candidates):
        return None, "four-digit birth year not found in the quoted source text (two-digit years are not expanded)"
    distinct = {(int(year), month, day) for _, year, month, day in candidates}
    if len(distinct) > 1:
        what = "conflicting dates of birth" if labels else "several dates and none is labeled as date of birth"
        return None, f"quoted source text contains {what}"
    if distinct.pop() != (dob.year, dob.month, dob.day):
        return None, "extracted value does not match the date in the quoted source text"
    return dob, None


def _check_npi(value: str, source: str) -> tuple[str | None, str | None]:
    npi = re.sub(r"[\s-]", "", value)  # strip formatting only; never alter digits
    if err := npi_error(npi):
        return None, f"NPI {err}"
    if npi not in _digits(source):
        return None, "extracted value is not supported by the quoted source text"
    return npi, None


_CHECKS = {
    "patient_first_name": _check_name,
    "patient_last_name": _check_name,
    "patient_dob": _check_dob,
    "provider_name": _check_name,
    "provider_npi": _check_npi,
}


def accept_fields(raw: dict, page_count: int) -> tuple[dict, list[dict]]:
    """Apply deterministic checks to the model output. Returns (accepted_fields, issues).

    `page_count` is the real number of pages in the uploaded PDF; every accepted value must cite one of them.
    """
    fields: dict = {}
    issues: list[dict] = []
    for name in REQUIRED_ORDER_FIELDS:
        item = raw.get(name) or {}
        value, source, note, page = item.get("value"), item.get("source_text"), item.get("note"), item.get("page")
        fields[name] = None
        if value is None or not str(value).strip():
            # The model's note says why (absent, illegible, or conflicting/ambiguous candidates).
            issues.append({"field": name, "message": f"not extracted: {note}" if note else "not found in document"})
            continue
        # A rejected value is shown to the reviewer in the issue, but never saved into the order field.
        if not source:
            issues.append({"field": name, "message": "extracted value has no supporting source text",
                           "rejected_value": str(value)})
            continue
        # bool is an int subclass; reject it explicitly. Page 0 is only meaningful for unextracted fields.
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= page_count:
            issues.append({"field": name, "rejected_value": str(value),
                           "message": f"cited page {page!r} is not a page of this document (1-{page_count})"})
            continue
        accepted, problem = _CHECKS[name](str(value), str(source))
        if problem:
            issues.append({"field": name, "message": problem, "rejected_value": str(value)})
        else:
            fields[name] = accepted
    return fields, issues
