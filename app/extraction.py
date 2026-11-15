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
  and page is the 1-based page it appears on. Use null for both when value is null.
- Do not swap first and last names: "DOE, JANE" means last name DOE, first name JANE."""


def _field_schema(description: str) -> dict:
    nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": nullable_str,
            "source_text": nullable_str,
            "page": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "note": nullable_str,
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


def _check_dob(value: str, source: str) -> tuple[date | None, str | None]:
    try:
        dob = date.fromisoformat(value.strip())
    except ValueError:
        return None, "extracted value is not a valid YYYY-MM-DD date"
    if err := dob_error(dob):
        return None, f"date of birth {err}"
    # The source must show the full 4-digit year (a 2-digit year leaves the century to guesswork)
    # and the day number.
    if not re.search(rf"(?<!\d){dob.year}(?!\d)", source):
        return None, "four-digit birth year not found in the quoted source text"
    if not re.search(rf"(?<!\d)0?{dob.day}(?!\d)", source):
        return None, "extracted value is not supported by the quoted source text"
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


def accept_fields(raw: dict) -> tuple[dict, list[dict]]:
    """Apply deterministic checks to the model output. Returns (accepted_fields, issues)."""
    fields: dict = {}
    issues: list[dict] = []
    for name in REQUIRED_ORDER_FIELDS:
        item = raw.get(name) or {}
        value, source, note = item.get("value"), item.get("source_text"), item.get("note")
        fields[name] = None
        if value is None or not str(value).strip():
            msg = "not found in document"
            issues.append({"field": name, "message": f"{msg}: {note}" if note else msg})
            continue
        if not source:
            issues.append({"field": name, "message": "extracted value has no supporting source text"})
            continue
        accepted, problem = _CHECKS[name](str(value), str(source))
        if problem:
            issues.append({"field": name, "message": problem})
        else:
            fields[name] = accepted
    return fields, issues
