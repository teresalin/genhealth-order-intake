import io
import os

import pypdf
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import extraction
from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import Order, User
from app.schemas import OrderCreate, OrderOut, OrderUpdate

router = APIRouter(prefix="/orders", tags=["orders"])
MAX_PAGES = 100


def _read_pdf_upload(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds {get_settings().max_upload_mb} MB")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "File is not a PDF")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Encrypted PDFs are not supported")
        pages = len(reader.pages)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "PDF could not be read")
    if pages == 0 or pages > MAX_PAGES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"PDF must have 1 to {MAX_PAGES} pages")
    return data


@router.post("/upload", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def upload_order(
    file: UploadFile = File(..., description="DME order PDF (scanned or digital)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an order from a PDF. Fields that are missing or fail validation are left null and
    listed in `validation_issues`; the order is then `needs_review` and can be completed with PATCH.
    The PDF itself is not stored."""
    pdf = _read_pdf_upload(file)
    try:
        raw = extraction.call_model(pdf)
    except extraction.ExtractionError as e:
        raise HTTPException(e.status_code, e.detail)
    fields, issues = extraction.accept_fields(raw)
    filename = os.path.basename(file.filename or "upload.pdf")[:255]
    order = Order(
        owner_id=user.id,
        source="pdf_upload",
        source_filename=filename,
        validation_issues=issues,
        extraction_raw=raw,
        **fields,
    )
    order.refresh_status()
    db.add(order)
    db.commit()
    return order


def get_owned_order(order_id: int, user: User, db: Session) -> Order:
    # Ownership is part of the query; another user's order is indistinguishable from a missing one (404).
    order = db.scalar(select(Order).where(Order.id == order_id, Order.owner_id == user.id))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(body: OrderCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = Order(owner_id=user.id, source="manual", validation_issues=[], **body.model_dump())
    order.refresh_status()
    db.add(order)
    db.commit()
    return order


@router.get("", response_model=list[OrderOut])
def list_orders(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Order).where(Order.owner_id == user.id).order_by(Order.id.desc()).limit(limit).offset(offset)
    return db.scalars(stmt).all()


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned_order(order_id, user, db)


@router.patch("/{order_id}", response_model=OrderOut)
def update_order(
    order_id: int, body: OrderUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    order = get_owned_order(order_id, user, db)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(order, field, value)
    # A validated correction resolves any earlier issue on that field.
    order.validation_issues = [i for i in order.validation_issues if i["field"] not in changes]
    order.refresh_status()
    db.commit()
    return order


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(get_owned_order(order_id, user, db))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
