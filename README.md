# GenHealth DME Order Intake API

A Python REST API for durable medical equipment (DME) order intake. Authenticated users create, read,
update and delete their own orders, or upload an order PDF (including scanned, multi-page faxes) from
which the API extracts the patient's first name, last name and date of birth, and the ordering
provider's name and NPI. Every extracted value is checked against the document evidence before it is
saved; anything missing, ambiguous or unsupported is left empty and flagged for review, never guessed.

**Public deployment:** https://genhealth-order-intake.onrender.com
(interactive API docs at [`/docs`](https://genhealth-order-intake.onrender.com/docs))

## Architecture

```
client ──HTTPS──▶ FastAPI app (Docker on Render)
                    ├─ /auth    register, JWT login            ─┐
                    ├─ /orders  CRUD, scoped to the caller      ├─▶ PostgreSQL (SQLAlchemy)
                    ├─ /orders/upload ─▶ PDF checks (pypdf)     │
                    │       └─▶ Claude API (PDF in, JSON out)    │
                    │       └─▶ deterministic acceptance checks ─┘
                    └─ /health  DB connectivity + deployed commit
```

| Path | Responsibility |
|---|---|
| `app/main.py` | App setup, router registration, `/health`, schema creation on startup |
| `app/config.py` | Settings from environment variables |
| `app/db.py` | SQLAlchemy engine and session |
| `app/models.py` | `User` and `Order` tables; order status rule |
| `app/auth.py` | Registration, login, JWT issue/verify, current-user dependency |
| `app/orders.py` | Order CRUD and PDF upload endpoint |
| `app/extraction.py` | Claude call, output schema, acceptance checks for each field |
| `app/validation.py` | Shared validators: names, NPI check digit, DOB range |
| `app/schemas.py` | Pydantic request/response models |
| `scripts/` | Synthetic test-PDF generator and a real-extraction evaluation runner (dev tools) |

## Technology stack

| Concern | Choice |
|---|---|
| Language / framework | Python 3.12, FastAPI, Pydantic v2 |
| Database | PostgreSQL in production (Render), SQLite for local runs and tests; SQLAlchemy 2.x ORM |
| Authentication | OAuth2 password flow issuing HS256 JWTs (PyJWT); passwords hashed with bcrypt |
| PDF handling | pypdf for upload checks (readable, not encrypted, page count) |
| Extraction | Anthropic Claude (`claude-opus-5-5` by default) via the official `anthropic` SDK, reading the PDF directly |
| Deployment | Docker image on Render, Render Postgres, defined in `render.yaml` |

## API

All `/orders` endpoints require `Authorization: Bearer <token>`. A user can only reach their own
orders; another user's order returns `404`, the same as a missing one.

| Method | Path | Description | Success |
|---|---|---|---|
| `GET` | `/` | Service name, description, and links to `/docs` and `/health` (no auth) | `200` |
| `GET` | `/health` | Database connectivity, deployed commit, whether extraction is configured | `200` (`503` if the DB is unreachable) |
| `POST` | `/auth/register` | Create a user: JSON `{"email", "password"}` (password 8–72 bytes) | `201` (`409` if the email exists) |
| `POST` | `/auth/token` | Log in: form fields `username` (your email) and `password` | `200` `{"access_token", "token_type": "bearer"}` |
| `GET` | `/auth/me` | The current user | `200` |
| `POST` | `/orders` | Create an order manually (all five fields required and validated) | `201` |
| `GET` | `/orders` | List your orders, newest first; `limit` (1–100, default 50), `offset` | `200` |
| `GET` | `/orders/{id}` | Get one of your orders | `200` |
| `PATCH` | `/orders/{id}` | Correct or complete fields (partial update; fields cannot be set to null) | `200` |
| `DELETE` | `/orders/{id}` | Delete one of your orders | `204` |
| `POST` | `/orders/upload` | Multipart field `file`: a PDF to extract an order from | `201` |

Order responses look like this (`validation_issues` is empty for a complete order):

```json
{
  "id": 4,
  "patient_first_name": "Olivia",
  "patient_last_name": "Carter",
  "patient_dob": null,
  "provider_name": "Maya Chen, MD",
  "provider_npi": "1230004560",
  "status": "needs_review",
  "source": "pdf_upload",
  "source_filename": "order.pdf",
  "validation_issues": [
    {"field": "patient_dob", "message": "not extracted: the dates of birth conflict ...", "rejected_value": null}
  ],
  "created_at": "2026-11-15T21:39:10.466387Z",
  "updated_at": "2026-11-15T21:39:10.466392Z"
}
```

### Authentication

```bash
BASE=https://genhealth-order-intake.onrender.com

# Register
curl -s -X POST "$BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "a-long-password"}'

# Log in (form-encoded, OAuth2 password flow; "username" is the email)
TOKEN=$(curl -s -X POST "$BASE/auth/token" \
  -d "username=you@example.com" -d "password=a-long-password" \
  | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

curl -s "$BASE/auth/me" -H "Authorization: Bearer $TOKEN"
```

Tokens expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (default 60). In `/docs`, use **Authorize** with
your email and password.

### Order CRUD

```bash
# Create (all fields required; NPI must have a valid check digit)
curl -s -X POST "$BASE/orders" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"patient_first_name": "Olivia", "patient_last_name": "Carter", "patient_dob": "1990-02-02",
       "provider_name": "Maya Chen, MD", "provider_npi": "1230004560"}'

# List (newest first) and get one
curl -s "$BASE/orders?limit=20&offset=0" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/orders/1" -H "Authorization: Bearer $TOKEN"

# Correct or complete fields; resolves any validation issue on the updated fields
curl -s -X PATCH "$BASE/orders/1" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"provider_npi": "1230004560"}'

# Delete (204, no body)
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE "$BASE/orders/1" -H "Authorization: Bearer $TOKEN"
```

Invalid input returns `422`: unknown fields, a name with digits or symbols, a date of birth in the
future or before 1900, or an NPI that is not 10 digits with a valid check digit.

### PDF upload

```bash
curl -s -X POST "$BASE/orders/upload" -H "Authorization: Bearer $TOKEN" \
  -F "file=@order.pdf;type=application/pdf"
```

Returns `201` with the created order (`complete` or `needs_review`). Other responses:
`401` no or invalid token · `413` larger than `MAX_UPLOAD_MB` (default 20) · `415` not a PDF ·
`422` unreadable or encrypted PDF, or not 1–100 pages · `502` extraction provider error ·
`503` extraction not configured or temporarily unavailable. Extraction typically takes 5–15 seconds;
on Render's free plan the first request after idling can take about a minute longer.

## Extraction and validation workflow

1. **Upload checks** (before any model call): size limit, `%PDF-` signature, readable by pypdf, not
   encrypted, 1–100 pages. The page count is kept for step 4.
2. **Extraction.** The PDF is sent to Claude, which reads both its text layer and page images (so
   image-only scans and filled-in form fields work). The system prompt treats all PDF content as
   untrusted data, limits the task to the five fields, and requires `null` rather than a guess when a
   value is absent, illegible or ambiguous (for example, two different DOBs, or an unclear choice
   between providers). The response is constrained to a JSON schema: for each field, `value`, a
   verbatim `source_text` quote, the `page`, and a `note`.
3. **Deterministic acceptance checks** (in `app/extraction.py`; the model's word is not trusted):
   - **Evidence present:** an extracted value needs a quote and a page between 1 and the real page count.
   - **Names:** allowed characters only, and the name must appear in the quote as one contiguous word
     sequence (case, punctuation and credentials normalized), so a name cannot be assembled from two
     people. When the patient's first and last name cite the same quote they must be adjacent there
     (`FIRST LAST` or `LAST, FIRST`).
   - **Date of birth:** valid date, not in the future, not before 1900, and the quote must contain
     exactly that date (month, day, four-digit year) right after a DOB label when one is present.
     Two-digit years are rejected (the century is never inferred); conflicting dates are rejected;
     order, signature and visit dates in the same quote are ignored.
   - **NPI:** 10 digits with a valid check digit, written directly after an NPI label in the quote.
     Phone, fax, Medicaid, SSN or unlabeled numbers are rejected, as are quotes with several different
     NPIs or several provider roles. A valid check digit shows the number is well-formed, not who it
     belongs to; NPIs are not looked up in NPPES.
4. **Save.** Accepted values are stored on the order. Missing or rejected fields are stored as `null`
   with an entry in `validation_issues`. The model's raw output is kept in the database for audit
   (it is not returned by the API).

### `complete` vs `needs_review`

- **`complete`**: all five fields are present and there are no validation issues.
- **`needs_review`**: at least one field is `null` or has an issue. A person completes the order with
  `PATCH /orders/{id}`; each validated correction clears the issue for that field, and the status
  becomes `complete` once nothing is outstanding.

### How missing, ambiguous and rejected values are handled

| Situation | Field value | `validation_issues` entry |
|---|---|---|
| Not in the document | `null` | `"not found in document"` |
| Model declined (illegible, conflicting, ambiguous) | `null` | `"not extracted: <model's reason>"` |
| Extracted but failed a check (bad NPI check digit, date not in quote, page out of range, ...) | `null` | the reason, plus `rejected_value` with what was extracted |

Rejected values are shown to the reviewer but never saved into the order fields.

## Data handling and privacy

- Uploaded PDFs are sent to Anthropic's API for extraction.
- The original PDF is not stored by this application; it is held in memory only for the request.
- Extracted patient and provider fields, validation issues, and the model's raw output (including the
  quoted evidence text) are stored in PostgreSQL, scoped to the uploading user.
- Logs record the model, token usage and request IDs for extraction calls, not document content.
- This is an assessment MVP. It has **not** been set up or reviewed as a HIPAA-compliant production
  environment (no BAA, audit logging, encryption-at-rest configuration review, or retention policy).
  **Use only synthetic patient information when testing.**

## Local setup

Requires Python 3.12 (matching the production image).

```bash
py -3.12 -m venv .venv           # or python3.12 -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements-dev.txt
cp .env.example .env             # then set ANTHROPIC_API_KEY (and JWT_SECRET)
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs. Without `DATABASE_URL`, a local SQLite file (`local.db`) is used.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `production` refuses to start with the default `JWT_SECRET` |
| `DATABASE_URL` | `sqlite:///./local.db` | SQLAlchemy URL; `postgres://` / `postgresql://` URLs are converted to the psycopg driver |
| `JWT_SECRET` | `change-me` | JWT signing key; set a long random value in production |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token lifetime |
| `BCRYPT_ROUNDS` | `12` | bcrypt cost factor |
| `ANTHROPIC_API_KEY` | empty | Required for `/orders/upload` (returns `503` without it) |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model used for extraction |
| `MAX_UPLOAD_MB` | `20` | Upload size limit |

### Database schema

Tables are created at startup with SQLAlchemy `Base.metadata.create_all()`. There is no migration
framework (such as Alembic): `create_all()` creates missing tables but does not alter existing ones,
so a schema change requires a migration tool or recreating the tables.

## Tests

```bash
pytest
```

135 offline tests (no network, no API key, no cost; the Claude call is replaced with canned output):
auth and token handling, order CRUD, cross-user isolation, upload checks, every acceptance check, and
`tests/fixtures/recorded_extractions.json`, real model outputs recorded from synthetic documents and
re-checked against the current validation rules.

Real-model evaluation is separate and costs API credits:

```bash
python scripts/make_synthetic_samples.py samples/synthetic          # fictional test PDFs
python scripts/eval_extraction.py . results.json samples/synthetic/*.pdf
```

`samples/` is gitignored; do not commit real patient documents.

## Deployment (Render)

The live service runs from `render.yaml` (a Render Blueprint): one Docker web service plus one Render
Postgres database, with `JWT_SECRET` generated by Render and `DATABASE_URL` wired from the database.

1. Push the repository to GitHub.
2. In Render: **New → Blueprint**, select the repository and branch `main`, and apply.
3. Set `ANTHROPIC_API_KEY` on the web service (it is deliberately not in the Blueprint).
4. Verify: `curl https://<service>.onrender.com/health` should show `"database_backend": "postgresql"`,
   `"extraction_configured": true` and `"commit"` equal to the deployed GitHub commit.

Pushes to `main` redeploy automatically.

## Known limitations

- Extraction accuracy has been checked on a small set of documents (one real scanned demo fax and
  synthetic forms, including degraded scans); it is not a measure of general reliability. Handwriting,
  non-English documents and PDFs with several patients have not been tested.
- The acceptance checks confirm each value matches the model's quote and cites a real page; for
  scanned pages they cannot confirm the quote actually appears on that page.
- Strict checks can send valid orders to review: dates written before their label or in day-first
  format, names extracted in a different order than written, a patient with a full middle name when
  first and last come from one quote, or an NPI quote naming more than one provider role.
- NPIs are checked for format and check digit only, not against the NPPES registry.
- No database migrations (see above); no rate limiting; no password reset or token revocation.
- Render's free plan sleeps when idle (slow first request), and its free Postgres database expires
  after a limited period.
- Not a HIPAA-compliant deployment (see Data handling and privacy).
