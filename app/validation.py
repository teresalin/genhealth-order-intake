"""Deterministic field checks shared by manual order input and PDF extraction."""
from datetime import date

MIN_DOB = date(1900, 1, 1)
MAX_NAME_LEN = 100
_NAME_PUNCTUATION = set(" '’.,-")


def normalize_name(value: str) -> str:
    return " ".join(value.split())


def name_error(value: str) -> str | None:
    if not value:
        return "is empty"
    if len(value) > MAX_NAME_LEN:
        return f"is longer than {MAX_NAME_LEN} characters"
    if not any(c.isalpha() for c in value):
        return "contains no letters"
    if not all(c.isalpha() or c in _NAME_PUNCTUATION for c in value):
        return "contains characters not allowed in a name"
    return None


def is_valid_npi(npi: str) -> bool:
    """10 digits whose last digit is the Luhn check digit over the '80840' prefix + first 9 digits (CMS spec)."""
    if len(npi) != 10 or not npi.isascii() or not npi.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + npi)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def npi_error(npi: str) -> str | None:
    return None if is_valid_npi(npi) else "must be 10 digits with a valid NPI check digit"


def dob_error(dob: date, today: date | None = None) -> str | None:
    today = today or date.today()
    if dob > today:
        return "is in the future"
    if dob < MIN_DOB:
        return f"is before {MIN_DOB.isoformat()}"
    return None
