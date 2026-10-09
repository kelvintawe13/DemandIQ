# DemandIQ Backend

FastAPI service that sits between the React client and the forecasting, policy-simulation and
explainability components. Live — `backend/app/main.py:135`, 177 paths (213 items).

## Status

**Fully implemented** — 177 paths (213 items), migrations `001`→`014_devices`,
`575 tests passed + 7 skipped`, `118 py`, `32 routers` (30 modules; `agent.py` and
`datasets.py` each export a second router), `333× EmptyState` where no data
(`backend/app/main.py:249`, `frontend/src/api/client.ts:1`).

## Tech Stack

- **Language**: Python 3.11
- **API**: FastAPI + Pydantic v2
- **ORM**: SQLAlchemy 2.0 + Alembic
- **Database**: PostgreSQL 15 + `citext`/`pgcrypto` (`database/schema.sql:2`)
- **Cache/Queue**: Redis + RQ (or Celery)
- **Storage**: S3-compatible object store
- **Auth**: OAuth2 + JWT, TOTP second factor

## Installation

```bash
npm run install:all        # root + frontend
cd backend && pip install -e ".[dev]"
# or via docker
docker compose up -d postgres redis
python3.11 -m alembic upgrade head
```

## Development

```bash
# One-command dev (postgres/redis + alembic + uvicorn :8000 + vite :5173)
bash scripts/dev.sh
# or
npm run dev

# Direct backend
python3.11 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# or via npm
npm run dev:backend

# Verify (ruff + black / mypy / pytest 575 + openapi 155→187 + console 187 + vite + pg)
npm run verify
bash scripts/verify.sh

# Regenerate console catalog after router changes
npm run sync:console
python3.11 backend/scripts/sync_console.py

# Run tests
pytest -q
python3.11 -m pytest -q   # 575 passed, 7 skipped

# Format / lint / type-check (whole tree — app, tests, scripts, migrations)
black app tests scripts migrations
ruff check app tests scripts migrations   # E4,E7,E9,F,I,UP,B008,DTZ,S
mypy app
```

## API Console

`api-console.html` is a live catalog — **213 items** from `openapi.json` (177 paths). Regenerate with
`npm run sync:console` (`backend/scripts/sync_console.py`) after any router change. Served at
`/console` (`backend/app/main.py:301`) and `/` for browsers. Also see `/docs` (Swagger) and
`/health`.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [API Endpoints](API_ENDPOINTS.md) — 177 paths → 213 items (sync via `backend/scripts/sync_console.py`)
- [Implementation Guide](IMPLEMENTATION_GUIDE.md)
- [Implementation Roadmap](IMPLEMENTATION_ROADMAP.md)
- [Agent Service](AGENT_SERVICE.md) — 14 agent paths + 10 billing paths

## Project Structure

```
backend/
├── app/
│   ├── main.py              # create_app + router registration (backend/app/main.py:249)
│   ├── config.py            # Settings from environment
│   ├── deps.py              # Shared dependencies
│   ├── database.py          # Session factory
│   ├── middleware/audit.py  # AuditMiddleware — audit_log + location_access_logs
│   ├── routers/             # 26 router modules (one per prefix) → 28 registered routers
│   │   ├── health.py        # /health + /api/v1/health
│   │   ├── auth.py          # /api/v1/auth
│   │   ├── access.py        # /api/v1/locations/access-logs + /audit-log
│   │   ├── ingest.py        # /api/v1/ingest (manual / file / image)
│   │   ├── forecasts.py
│   │   ├── policies.py
│   │   ├── quality.py / alerts.py
│   │   ├── reports.py / analytics.py
│   │   ├── sources.py / connectors.py / apikeys.py
│   │   ├── governance.py    # /change-requests + /members + /pipeline/*
│   │   ├── me.py            # /api/v1/me/*
│   │   ├── billing.py       # /api/v1/billing — 10 paths
│   │   ├── agent.py         # /api/v1/agent + /api/v1/admin/agent — 14 paths
│   │   ├── models.py / experiments.py / features.py
│   │   ├── datasets.py      # /datasets + waste_router /waste/summary
│   │   └── locations.py / menu.py
│   ├── services/            # Business logic
│   │   ├── ingestion/file.py + image.py + manual.py + normaliser.py
│   │   ├── forecasting/loader.py + conformal.py + persist.py
│   │   ├── policy/simulate.py + explain/shap.py
│   │   ├── reporting/ + delivery/webhooks.py + governance.py
│   │   └── feature_build.py
│   ├── models/              # SQLAlchemy ORM (41 model classes / 20 modules, backend/app/models/__init__.py)
│   ├── schemas/             # Pydantic v2 request/response
│   ├── jobs/                # Queue workers and scheduled tasks
│   └── security/            # Auth, rate limits, jwt, hashing
├── migrations/versions/     # 001_initial → … → 014_devices (single head)
├── scripts/sync_console.py  # Live catalog sync → api-console.html
├── tests/                   # 34 modules — 575 passed, 7 skipped
├── pyproject.toml           # ruff/black scope + the mypy contract
└── api-console.html         # Live catalog 213 items
```

## Environment Variables

Required environment variables (see `IMPLEMENTATION_GUIDE.md` for full list). `DATABASE_URL` is
overridden in `docker-compose.yml:47`; frontend uses `VITE_API_URL` (`docker-compose.yml:73`,
`frontend/src/api/client.ts:1`):

```
DATABASE_URL=postgresql+psycopg2://demandiq:demandiq_password@postgres:5432/demandiq  # docker-compose.yml:47
REDIS_URL
OBJECT_STORE_URL
OBJECT_STORE_KEY
OBJECT_STORE_SECRET
JWT_SECRET
JWT_TTL_MINUTES
REFRESH_TTL_DAYS
TOTP_ISSUER
TURNSTILE_SECRET
GOOGLE_OAUTH_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET
MODEL_REGISTRY_URI
DEFAULT_COVERAGE=0.90
DEFAULT_PREP_QUANTILE=0.80
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
WEBHOOK_SIGNING_ALGO=hmac-sha256
VITE_API_URL=http://localhost:8000                                   # docker-compose.yml:73 frontend/src/api/client.ts:1
```
