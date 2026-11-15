from datetime import date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.validation import dob_error, name_error, normalize_name, npi_error


def _check_name(v: str) -> str:
    v = normalize_name(v)
    if err := name_error(v):
        raise ValueError(f"name {err}")
    return v


def _check_npi(v: str) -> str:
    v = v.strip()
    if err := npi_error(v):
        raise ValueError(f"NPI {err}")
    return v


def _check_dob(v: date) -> date:
    if err := dob_error(v):
        raise ValueError(f"date of birth {err}")
    return v


PersonName = Annotated[str, AfterValidator(_check_name)]
NPI = Annotated[str, AfterValidator(_check_npi)]
DOB = Annotated[date, AfterValidator(_check_dob)]


# ---- auth ----

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @model_validator(mode="after")
    def password_fits_bcrypt(self) -> "UserCreate":
        if len(self.password.encode()) > 72:
            raise ValueError("password must be at most 72 bytes")
        return self


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- orders ----

class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_first_name: PersonName
    patient_last_name: PersonName
    patient_dob: DOB
    provider_name: PersonName
    provider_npi: NPI


class OrderUpdate(BaseModel):
    """Partial update; used to correct or complete an order (e.g. one flagged needs_review)."""
    model_config = ConfigDict(extra="forbid")
    patient_first_name: PersonName | None = None
    patient_last_name: PersonName | None = None
    patient_dob: DOB | None = None
    provider_name: PersonName | None = None
    provider_npi: NPI | None = None

    @model_validator(mode="after")
    def no_explicit_nulls(self) -> "OrderUpdate":
        nulls = [f for f in self.model_fields_set if getattr(self, f) is None]
        if nulls:
            raise ValueError(f"fields cannot be cleared: {', '.join(sorted(nulls))}")
        return self


class ValidationIssue(BaseModel):
    field: str
    message: str


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_first_name: str | None
    patient_last_name: str | None
    patient_dob: date | None
    provider_name: str | None
    provider_npi: str | None
    status: str
    source: str
    source_filename: str | None
    validation_issues: list[ValidationIssue]
    created_at: datetime
    updated_at: datetime
