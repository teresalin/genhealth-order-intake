from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Fields an order needs before it counts as complete.
REQUIRED_ORDER_FIELDS = (
    "patient_first_name",
    "patient_last_name",
    "patient_dob",
    "provider_name",
    "provider_npi",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Nullable: a PDF may not contain a field, and we never guess it.
    patient_first_name: Mapped[str | None] = mapped_column(String(100))
    patient_last_name: Mapped[str | None] = mapped_column(String(100))
    patient_dob: Mapped[date | None] = mapped_column(Date)
    provider_name: Mapped[str | None] = mapped_column(String(100))
    provider_npi: Mapped[str | None] = mapped_column(String(10))

    status: Mapped[str] = mapped_column(String(20))  # "complete" | "needs_review"
    source: Mapped[str] = mapped_column(String(20), default="manual")  # "manual" | "pdf_upload"
    source_filename: Mapped[str | None] = mapped_column(String(255))
    # [{"field": ..., "message": ...}] for values that were missing or failed validation.
    validation_issues: Mapped[list] = mapped_column(JSON, default=list)
    # Raw model output for audit, kept separate from the validated fields above.
    extraction_raw: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def refresh_status(self) -> None:
        missing = any(getattr(self, f) is None for f in REQUIRED_ORDER_FIELDS)
        self.status = "needs_review" if missing or self.validation_issues else "complete"
