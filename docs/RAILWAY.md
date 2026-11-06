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

## Two ways to configure it, and they must not be mixed

- **`.railway/railway.ts`** — the project configuration, evaluated by the Railway CLI.
  This is the supported form and the one in this repository: §9. It creates both
  services, both data stores and every variable in one `apply`.
- **The dashboard** — sections 1 to 5 below, step by step, for doing the same by hand.
  Use one or the other for a given service, not both.

`railway.toml` / `railway.json` are the retired per-service format. Railway stops reading
them on **2026-12-01**, and new services cannot opt into them at all, so do not add one:
it would be ignored without an error.


---

## 0. Before you start

1. **Push the code.** Railway deploys the git commit, not your working tree. This
   deployment also needs one file that is not tracked yet:

   ```bash
   git add backend/artifacts/serving_bundle.joblib \
           package-lock.json \
           .dockerignore \
           frontend/Dockerfile.railway \
           frontend/entrypoint.railway.sh \
           frontend/nginx.railway.conf.template \
           model/Dockerfile.railway model/.dockerignore model/serve_bundles.py \
           scripts/publish_bundle.sh .github/workflows/publish-bundle.yml \
           .railway/railway.ts \
           docs/RAILWAY.md
   git commit -m "Add Railway deployment configuration"
   git push
   ```

   `package-lock.json` is the root workspace lockfile: `frontend/Dockerfile.railway`
   copies it and runs `npm ci`, and the web service builds from the git commit, so the
   build fails without it. It used to be gitignored for this repository; it is not any
   more.

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
| `MODEL_BUNDLE_URL` | *(optional)* `http://model.railway.internal:9000/latest/serving_bundle.joblib` | Only with the model service in §10. It is fetched once at startup and preferred over `MODEL_BUNDLE_PATH`, which stays the fallback if the fetch fails. Leave it unset to serve the export baked into the image. |
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

### The same checks, automated

`scripts/smoke_deployed.sh` runs those probes in one pass and fails on the
`serving.mode` value rather than printing it:

```bash
scripts/smoke_deployed.sh https://<web>.up.railway.app
```

It waits for the rollout (default 600 s, `WAIT_SECONDS` to change), then asserts the API
answers, `database: ok`, `serving.mode: trained`, and that `/healthz`, `/app/` and
`/docs` are served through the proxy.

To run it after every deploy, set the **repository variable** `SMOKE_BASE_URL` to the web
service URL (**Settings → Secrets and variables → Actions → Variables**).
`.github/workflows/smoke.yml` then runs it whenever `CI` succeeds on `main`, and on demand
via *Actions → Deployed smoke check → Run workflow*. Without the variable the job skips
with a notice instead of failing every push.

It verifies that the deployment is serving correctly, not that the newest commit is the
live one — the API does not expose the deployed git SHA, so a smoke that runs while
yesterday's release is still serving will pass.

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

- Two services + Postgres + Redis is four billable resources (five with the model
  service in §10, whose image is small: roughly 600 MB serving-only). The API image is the heavy
  one: `lightgbm` + `pandas` + Tesseract is roughly 400–600 MB, and the first build takes
  several minutes. Subsequent builds reuse the dependency layer unless
  `pyproject.toml` changes.
- One API replica is enough. The app keeps model state in-process; scaling out is safe
  for reads but only shares rate-limit/MFA/quota state through Redis.
- There is no object storage configured, so uploaded images are processed and discarded.
  `OBJECT_STORE_*` would be the place to add durable storage.

---

## 9. Project configuration in code (`.railway/railway.ts`)

The file in this repository declares the whole project, so one `apply` builds the same
state as sections 1 to 3 done by hand:

| Resource | What the file sets |
|---|---|
| `api` | GitHub source, root directory `backend`; builds `backend/Dockerfile`; the §2 start command; pre-deploy `alembic upgrade head`; healthcheck `/api/v1/health` at 300 s; the §2 variables |
| `web` | GitHub source at the repository root; builds `frontend/Dockerfile.railway`; healthcheck `/healthz`; `API_UPSTREAM=api.railway.internal:8000`; `PORT=8080`; `VITE_API_URL` left unset |
| `model` | GitHub source, root directory `model`; builds `model/Dockerfile.railway`; healthcheck `/healthz`; volume `bundles` at `/data`; `INSTALL_TRAINING=0`; `BUNDLE_ADMIN_TOKEN` preserved |
| `postgres` | Managed PostgreSQL; `api` reads `DATABASE_URL` from it |
| `redis` | Managed Redis; `api` reads `REDIS_URL` from it |

Prerequisites: `npm install` (the `railway` npm package here is the IaC SDK behind
`railway/iac`, not the CLI) and Railway CLI 5.2 or newer
(`npm i -g @railway/cli`, or `brew install railway`).

```bash
railway login
railway link          # create the demandiq project, or select it
npm run railway:plan  # review: services, databases and variables, no deletions
npm run railway:apply # create them and start the first deploy
```

Things the file cannot do:

0. **`BUNDLE_ADMIN_TOKEN`** (only with the model service in §10). It is `preserve()` and
   the service refuses uploads until it is set, so a first apply leaves the service
   serving and publishing disabled. Set it on the model service, then publish.
1. **`JWT_SECRET`.** It is `preserve()`, so the file never holds a secret and never
   overwrites one. On a fresh project the API's first deploy stops, because
   `APP_ENV=production` refuses a missing or weak secret. Set one on the API service
   (**Variables → `JWT_SECRET`**, 32+ characters, e.g. `openssl rand -base64 48`), then
   `railway redeploy --service api --yes`.
2. **Generated domains and seeding.** §3 *Networking* and §4 apply as written; the file
   does not create Railway-generated domains.

Rules to keep in mind:

- An apply is authoritative for the resources in the file: a service or database the file
  no longer declares is deleted. Add a new resource to `resources` in the same change
  that introduces it.
- `railway config plan` refuses to run while any service is managed by `railway.toml` or
  `railway.json`. There are none in this repository and none should be added.
- Sections 2 and 3 and this file describe the same state. Edit one, then plan again,
  rather than leaving the other stale.

---

## 10. Optional: the model service

Nothing here is required to serve the trained model. The API loads the export baked into
its image (`backend/artifacts/serving_bundle.joblib`) and runs LightGBM inference
in-process; `serving.mode: trained` in `/api/v1/health` is the proof, with or without this
service.

Add the service when a published bundle should not need an API rebuild:

| Settings → | Value |
|---|---|
| Source → Root Directory | `model` |
| Source → Branch | `main` |
| Build → Dockerfile Path | `Dockerfile.railway` |
| Deploy → Healthcheck Path | `/healthz` |
| Deploy → Healthcheck Timeout | `120` |
| Volume | mount at `/data` |

| Variable | Value | Why |
|---|---|---|
| `PORT` | `9000` | Matches the private-network port in `MODEL_BUNDLE_URL`. |
| `BUNDLES_DIR` | `/data/bundles` | The volume mount plus a subdirectory, so a volume can be reused for other things. |
| `BUNDLE_ADMIN_TOKEN` | a fresh random string | Required to publish. Until it is set, `POST /bundles` answers `503` and the service only serves. |
| `INSTALL_TRAINING` | `0` | **Build argument.** `0` installs what it takes to *read* a bundle. `1` also installs the training extras (lightgbm, catboost, xgboost, scikit-learn, shap) so the pipeline can run inside the service — roughly 1 GB more, minutes longer to build. |

What it does:

- `GET /healthz` — liveness, plus whether a bundle is on the volume.
- `GET /latest/serving_bundle.joblib` — the export the API fetches.
- `GET /bundles` — every export ever published (they are kept under `exports/`).
- `POST /bundles` — validate a `serving_bundle.joblib` and make it latest. The body is
the file; the header is `X-Bundle-Token`. A file that is not a demandiq bundle, or one
older than the API serves, is rejected with `422` before it reaches the volume, because
the API treats an unloadable bundle at a configured source as a configuration error.

### Publishing a re-export

`scripts/publish_bundle.sh` uploads a bundle and then reads it back, so a green
run means the service is serving exactly the bytes that were sent, not merely that
the upload answered `201`:

```bash
MODEL_SERVICE_URL="https://<model>.up.railway.app" \
BUNDLE_ADMIN_TOKEN="<the service variable>" \
  bash scripts/publish_bundle.sh backend/artifacts/serving_bundle.joblib

# the API fetches once per boot, so roll it out explicitly
railway redeploy --service api --yes
```

Exit codes: `1` configuration (no URL or token, missing file, unreachable service),
`2` the service rejected the upload (`401`/`503` token, `422` not a bundle), `3` the
service is serving different bytes than were sent.

### Publishing from CI

`.github/workflows/publish-bundle.yml` retrains with the same arguments as
`make train-model` (`model/data/recruit` is committed, so the inputs match the
recorded run), exports the bundle, publishes it, and keeps the export as a build
artifact. It is manual only — **Actions → Publish model bundle → Run workflow** —
because it replaces the model the API serves; a push should not do that.

Configure two things once, under **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Variable | `MODEL_SERVICE_URL` | The model service's public URL. |
| Secret | `BUNDLE_ADMIN_TOKEN` | The same string as the service's `BUNDLE_ADMIN_TOKEN`. |

The workflow checks both before training starts and fails there rather than after
ten minutes of CPU. It does **not** redeploy the API: run
`railway redeploy --service api --yes` afterwards, which is also the rollback
boundary — until then the previous export keeps answering, and every published
export stays on the volume (`GET /bundles`).

It also does not touch `backend/artifacts/serving_bundle.joblib`, the fallback in
the API image. When a new model should be the fallback too, run `make train-model`
locally, copy `model/artifacts/runs/latest/serving_bundle.joblib` over it, and
commit — otherwise the fallback stays at whatever was last committed.

To retrain inside the service, set `INSTALL_TRAINING=1`, redeploy it, and run the
pipeline there with the same arguments as `make train-model`:

```bash
python -m diq_model.train train --data-root data/recruit \
  --checkpoints /data/runs/latest --test-start 2017-02-01 --seed 42 --force
python scripts/export_serving_bundle.py --artifacts /data/runs/latest
curl -X POST http://127.0.0.1:9000/bundles -H "X-Bundle-Token: $BUNDLE_ADMIN_TOKEN" \
  --data-binary @/data/runs/latest/serving_bundle.joblib
```

Failure behaviour, which is what makes this safe to add:

- The API fetches once at startup. If the model service is down, the export in the API
  image answers instead and `/api/v1/health` stays `200` `trained`; the boot log records
  the failed fetch. A model-service restart cannot take forecasting down.
- A cache file from an earlier boot is not treated as a fetch. Only a bundle this process
  actually downloaded is served, so a stale file cannot masquerade as the current model.
- Inference does not go through the model service: it serves a file, the API loads it.
  There is no extra network hop per forecast.
