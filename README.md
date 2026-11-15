# GenHealth DME Order Intake API

A small Python REST API for durable medical equipment (DME) order intake: CRUD on orders,
PDF upload with extraction of patient and ordering-provider details, validation that never
guesses missing values, and per-user isolation behind authentication.

> Status: **scaffold**. Only `GET /health` is implemented so far. See "Planned API" below.

## Stack

| Concern | Choice | Why |
|---|---|---|
| Web framework | FastAPI + Pydantic v2 | Typed request/response validation, auto OpenAPI docs at `/docs` |
| Persistence | SQLAlchemy 2.0 | SQLite locally and in tests, Postgres in production via `DATABASE_URL` |
| Auth | JWT bearer tokens, bcrypt-hashed passwords | Stateless, simple to test with `curl` |
| PDF extraction | Claude API (native PDF input: text layer + page images) | Handles scanned faxes and multi-page documents without a separate OCR stack |
| Validation | Deterministic checks after extraction | NPI Luhn check, real/plausible DOB, non-empty names; missing fields stay `null` and are flagged |
| Deployment | Docker image on Render + Render Postgres (`render.yaml`) | One-click Blueprint deploy, HTTPS URL, managed database |

## Local development

Requires Python 3.12 (matches the production image).

```bash
py -3.12 -m venv .venv           # or python3.12 -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements-dev.txt
cp .env.example .env             # then edit values
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/health and http://127.0.0.1:8000/docs.

### Tests

```bash
pytest
```

### Docker

```bash
docker build -t order-intake .
docker run --rm -p 8000:8000 --env-file .env order-intake
```

## Configuration

All settings come from environment variables (see `.env.example`):

| Variable | Purpose |
|---|---|
| `APP_ENV` | `production` enforces a non-default `JWT_SECRET` |
| `DATABASE_URL` | SQLAlchemy URL; `postgres://` URLs are normalized to the psycopg driver |
| `JWT_SECRET`, `ACCESS_TOKEN_EXPIRE_MINUTES` | Token signing and lifetime |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | PDF extraction |
| `MAX_UPLOAD_MB` | Upload size limit |

## Deployment (Render)

1. Push this repository to GitHub.
2. In Render: **New → Blueprint**, select the repo. `render.yaml` creates the web service and Postgres.
3. Set `ANTHROPIC_API_KEY` in the service's environment (it is intentionally not in the Blueprint).
4. Verify: `curl https://<service>.onrender.com/health`.

Note: Render free instances sleep when idle (first request can take ~1 minute) and free
Postgres databases expire after a limited period; use a paid plan for anything beyond evaluation.

## Planned API

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create a user |
| POST | `/auth/token` | Exchange credentials for a JWT |
| POST | `/orders` | Create an order |
| GET | `/orders` | List the caller's orders |
| GET/PATCH/DELETE | `/orders/{id}` | Read / update / delete the caller's order (others' orders return 404) |
| POST | `/orders/upload` | Upload a PDF; extract, validate, and create an order |

## Known limitations

- Schema is created on startup (`create_all`); no migration tool yet.
