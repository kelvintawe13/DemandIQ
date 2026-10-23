# Deploying DemandIQ on Railway

Two services, two plugins, one project:

```
browser ──► web  (nginx + built SPA)          public: https://<web>.up.railway.app/app/
              │  /api/  ── private network ──►  api (FastAPI)   public: https://<api>.up.railway.app/docs
                                                 │
                                                 ├── Postgres  (plugin)
                                                 └── Redis     (plugin, optional)
```

- **web** serves the static bundle under `/app/` and reverse-proxies `/api/`, `/docs`
  and `/console` to **api** over `api.railway.internal`. The browser therefore only
  ever talks to one origin, so there is no CORS preflight and no second domain to keep
  in sync.
- **api** runs the FastAPI app from the trained serving bundle, with the schema migrated
  by `alembic upgrade head` before each deploy.

Everything below is dashboard configuration. `railway.toml` / `railway.json` are **not
used**: Railway has deprecated config-as-code and new services do not read it (hard
cutoff 2026-12-01). Do not add one.

---

## 0. Before you start

1. **Push the code.** Railway deploys the git commit, not your working tree. This
   deployment also needs one file that is not tracked yet:

   ```bash
   git add backend/artifacts/serving_bundle.joblib \
           .dockerignore \
           frontend/Dockerfile.railway \
           frontend/entrypoint.railway.sh \
           frontend/nginx.railway.conf.template \
           docs/RAILWAY.md
   git commit -m "Add Railway deployment configuration"
   git push
   ```

   `backend/artifacts/serving_bundle.joblib` is a copy of
   `model/artifacts/runs/latest/serving_bundle.joblib`. It has to be copied because
   `model/artifacts/` is gitignored and the API image's build context is `backend/`,
   which cannot reach `../model`. Without it the API still boots, but every forecast
   is a rolling-mean stub — and `/api/v1/health` will say so.

2. Have a Railway account with the GitHub repo connected, and the repo's `main` branch
   selected. Railway builds on every push to that branch.

---

## 1. Create the project and the data stores

1. **New Project → Empty Project**, name it `demandiq`.
2. **+ New → Database → Add PostgreSQL.** Wait for it to go green.
3. **+ New → Database → Add Redis.** Optional, but recommended: without it, rate limits,
   MFA challenge state and agent quota counters live in each process's memory, so they
   are not shared across a restart or a second replica. The app degrades rather than
   failing (`backend/app/services/kv.py`).

Leave both without public networking. They are reachable over the private network only.

---

## 2. The API service

**+ New → GitHub Repo → your DemandIQ repo.**

Then, on the service (rename it `api` — the frontend's default upstream name assumes
this):

### Settings → Source
| Field | Value |
|---|---|
| Root Directory | `backend` |
| Branch | `main` |

Root directory `backend` means the build context is `backend/`, which is exactly what
`backend/Dockerfile` expects. It also picks up `backend/.dockerignore` (keeps `.env` out
of the image).

### Settings → Build
| Field | Value |
|---|---|
| Builder | Dockerfile (detected automatically from `backend/Dockerfile`) |
| Dockerfile Path | `Dockerfile` |

### Variables

Add these on the **Variables** tab. Railway reference syntax (`${{Service.VAR}}`) is
resolved at deploy time, so the password never appears in this file or in the repo.

| Variable | Value | Why |
|---|---|---|
| `APP_ENV` | `production` | Turns on the boot checks: a weak `JWT_SECRET` or an unloadable bundle aborts the deploy instead of serving wrong numbers. |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | The plugin's own DSN. |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Omit if you skipped Redis. The app falls back to in-process state. |
| `JWT_SECRET` | a fresh 32+ char random string | `openssl rand -base64 48`. Must not contain `dev-only-change-me`. |
| `PORT` | `8000` | Pinned so the private-network port the frontend dials is deterministic. |
| `INSTALL_EXTRAS` | `model ocr` | **Build argument** (declared as `ARG` in the Dockerfile, so Railway passes it at build time). `model` installs lightgbm/pandas/joblib so the bundle can actually run; `ocr` installs pytesseract **and**, via the Dockerfile's runtime stage, the `tesseract-ocr` OS packages — without them photo ingestion raises `ImportError`. |
| `MODEL_BUNDLE_PATH` | `/app/artifacts/serving_bundle.joblib` | Explicit, so a bundle that fails to load fails the healthcheck instead of silently falling back to the stub. |
| `CORS_ORIGINS` | `["https://<web>.up.railway.app"]` | Not strictly needed while the proxy is in place (same origin), but it is the correct value if you ever call the API directly. Must be **JSON**, not comma-separated. |
| `TRUSTED_PROXY_HEADERS` | `true` | Railway's edge terminates TLS and the container port is not directly reachable, so `X-Forwarded-For` is trustworthy — rate limiting then buckets real client IPs instead of putting everyone in one bucket. |
| `LOG_LEVEL` | `INFO` | Boot logs report the serving mode. |
| `JOBS_ENABLED` | `false` | See §5 if you want scheduled jobs. |

Do **not** set `VITE_API_URL` here — that belongs to the frontend.

### Settings → Deploy

| Field | Value |
|---|---|
| Custom Start Command | `sh -c 'exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"'` |
| Pre-deploy Command | `alembic upgrade head` |
| Healthcheck Path | `/api/v1/health` |
| Healthcheck Timeout | `300` |
| Restart Policy | `On Failure` |

Notes:

- The start command exists because the image's `CMD` binds port 8000 and knows nothing
  about proxies. `--proxy-headers --forwarded-allow-ips="*"` is safe **only** because
  Railway's public port is an HTTP proxy, never a raw published port; the Dockerfile
  deliberately does not bake it in for that reason.
- `alembic upgrade head` is the only thing that creates the schema. There are 20
  revisions in `backend/migrations/`, and nothing in the app calls `create_all` at
  startup, so a deploy without this step gives you a running API with no tables. It runs
  in its own container, with your variables and private-network access, before the new
  deployment takes traffic. If it fails, the deploy stops and the old one keeps serving.
- Healthcheck `/api/v1/health` (not `/health`) is what makes a broken bundle a failed
  deploy: it answers `503 degraded` when `MODEL_BUNDLE_PATH` is set but unloadable.

### Settings → Networking

**Generate Domain.** The API's public URL is only used for `/docs`, the OpenAPI console
at `/console`, and debugging — the browser normally reaches it through the web service.
The target port should already be `8000` (the `PORT` variable); set it if not.

---

## 3. The frontend service

**+ New → GitHub Repo → the same repo.** Rename it `web`.

### Settings → Source
| Field | Value |
|---|---|
| Root Directory | *(leave empty — repo root)* |
| Branch | `main` |

The root directory is the repo root on purpose: dependencies are an npm workspace
hoisted to the root `package-lock.json`, so there is no `frontend/package-lock.json` to
install from. The root `.dockerignore` is what keeps that context a few MB instead of
~1.7 GB (`model/` alone is ~943 MB, and a dev machine's `node_modules` is copied along
otherwise).

### Settings → Build
| Field | Value |
|---|---|
| Dockerfile Path | `frontend/Dockerfile.railway` |

### Variables
| Variable | Value | Why |
|---|---|---|
| `API_UPSTREAM` | `api.railway.internal:8000` | The private-network address of the API service. Must match the API's `PORT` (`8000`) and the service name (`api`). |
| `PORT` | `8080` | Optional; the entrypoint defaults to 8080 anyway. Railway may inject its own. |
| `VITE_API_URL` | *(leave empty)* | Empty compiles relative `/api/v1/...` calls into the bundle, which the nginx proxy forwards to `API_UPSTREAM`. Setting it to an absolute URL turns off the proxy path and re-introduces CORS. |
| `VITE_TURNSTILE_SITE_KEY` | optional | Public Turnstile site key. Build argument. |
| `VITE_GOOGLE_CLIENT_ID` | optional | Public Google OAuth client id. Build argument. |

`VITE_*` values are compiled into the bundle: changing one requires a redeploy, not just
a restart.

### Settings → Deploy
| Field | Value |
|---|---|
| Healthcheck Path | `/healthz` |
| Restart Policy | `On Failure` |

### Settings → Networking
**Generate Domain**, target port `8080`. Open `https://<web>.up.railway.app/` — it
redirects to `/app/`.

The container renders its nginx config at start-up (`frontend/entrypoint.railway.sh`)
from `API_UPSTREAM` and the container's own `/etc/resolv.conf`, which is why it works
here and the compose config (`frontend/nginx.conf`, hardcoded to `backend:8000` and
Docker's `127.0.0.11`) does not. The start-up log line states the port, upstream and
resolver it chose — check it first when `/api/` returns 502.

---

## 4. First run: seed a sign-in

A migrated database has no users, so the UI has nothing to sign in with. Seeding uses the
same scripts as local development, run inside the API container so they can reach the
private Postgres:

```bash
railway login
railway link                     # pick the project + production environment
railway ssh --service api
# inside the container:
python scripts/seed_site_admin.py     # admin@demandiq.app / Admin@2026!
python scripts/seed_demo_roles.py     # one account per role
python scripts/seed_demo_data.py      # fills the operator screens
exit
```

**If this is not a throwaway demo, override the published passwords first** —
`seed_site_admin.py` reads `DEMANDIQ_ADMIN_EMAIL`, `DEMANDIQ_ADMIN_PASSWORD`,
`DEMANDIQ_DEMO_EMAIL`, `DEMANDIQ_DEMO_PASSWORD`. Set them as service variables, then
seed.

All three scripts are idempotent and never delete.

---

## 5. Optional: the background worker

`JOBS_ENABLED=false` on the API keeps scheduled work off. If you want connector sync,
recalibration, drift checks and report delivery, create a **third service from the same
repo** with the same root directory and Dockerfile, then:

- Variables: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `APP_ENV`, `INSTALL_EXTRAS`,
  `MODEL_BUNDLE_PATH` — the same values as the API.
- Start Command: `python -m app.jobs.worker`
- No public domain, no healthcheck path (it runs no HTTP server).
- Do **not** also set `JOBS_ENABLED=true` on the API: every job would then fire twice.

---

## 6. Verify the deployment

```bash
# API: schema reachable, trained bundle actually loaded
curl -s https://<api>.up.railway.app/api/v1/health
# → {"status":"ok","database":"ok",
#    "serving":{"mode":"trained","algorithm":"lightgbm",
#                "version":"lightgbm-fs-2026.09.2-it332",...}}
```

`serving.mode` is the honest check: `"trained"` means the bundle is loaded and serving,
`"stub"` means none was found, `"error"` means `MODEL_BUNDLE_PATH` was set and could not
be loaded (this also fails the deploy healthcheck).

```bash
# Web tier: static shell, and the same API through the proxy
curl -sI https://<web>.up.railway.app/healthz                  # 200
curl -s  https://<web>.up.railway.app/app/ | head -5           # the SPA shell
curl -s  https://<web>.up.railway.app/api/v1/health            # same JSON as above
```

Then, in the browser:

1. `https://<web>.up.railway.app/` → sign in with the seeded admin.
2. **Forecast** — a point forecast plus its 90% interval, with a provenance line saying
   which model produced it.
3. **Ingestion → Photo** — upload a photo of a written log. Rows arrive in the preview
   with per-row confidence; nothing is written until you commit.
4. **Admin → Model Registry / Experiments** — the trained run's metadata.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `web` returns **502** on `/api/...` | Upstream name/port wrong, or the API is down | Check the `web` start-up log line for the upstream it rendered. It must be `api.railway.internal:8000`; that means the API service is named `api` and has `PORT=8000`. |
| `web` crash-loops with `host not found in upstream` | You are on the compose nginx config, not the Railway one | Dockerfile Path must be `frontend/Dockerfile.railway`. |
| Deploy fails at **pre-deploy** | `alembic upgrade head` could not reach Postgres | `DATABASE_URL` must be the `${{Postgres.DATABASE_URL}}` reference, and the plugin must be running. |
| Healthcheck fails, log says `ServingConfigurationError` | `MODEL_BUNDLE_PATH` is set but the file is missing/unloadable | The file must be committed at `backend/artifacts/serving_bundle.joblib` and the image rebuilt. |
| `/api/v1/health` says `serving.mode: "stub"` | `model` extra missing or bundle path wrong | `INSTALL_EXTRAS` must include `model`; check the build log for the lightgbm install. |
| Photo ingestion errors, log mentions `pytesseract` / `tesseract` | OCR not installed in the image | `INSTALL_EXTRAS` must include `ocr`; the build log should show `tesseract-ocr` being apt-installed. |
| Uploads fail with **413** | Body larger than nginx's limit | The Railway config sets `client_max_body_size 50m`; if you raised a backend cap, raise this too. |
| Sign-in works but every screen is empty | Database migrated but not seeded | Run §4. |
| Sign-in redirects to `http://...` | Proxy headers not trusted | Keep `--proxy-headers --forwarded-allow-ips="*"` in the start command and `TRUSTED_PROXY_HEADERS=true`. |
| Second replica behaves oddly (rate limits, MFA) | No Redis | Add the Redis plugin and `REDIS_URL`. |

Logs: **Service → Deployments → the active deployment → Logs**. The API prints its
serving mode at boot; the frontend prints its rendered upstream.

---

## 8. Cost and scaling notes

- Two services + Postgres + Redis is four billable resources. The API image is the heavy
  one: `lightgbm` + `pandas` + Tesseract is roughly 400–600 MB, and the first build takes
  several minutes. Subsequent builds reuse the dependency layer unless
  `pyproject.toml` changes.
- One API replica is enough. The app keeps model state in-process; scaling out is safe
  for reads but only shares rate-limit/MFA/quota state through Redis.
- There is no object storage configured, so uploaded images are processed and discarded.
  `OBJECT_STORE_*` would be the place to add durable storage.
