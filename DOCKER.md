# Running DemandIQ in containers

One command brings up Postgres, Redis, the schema migrations, the API and the web
tier:

```bash
cp .env.example .env      # optional — every value has a working default
make docker-up            # or: npm run docker:up
```

Then:

| URL | What |
|---|---|
| <http://localhost:5173/> | the product UI (redirects to `/app/`) |
| <http://localhost:5173/console> | the OpenAPI console (served by the API, proxied) |
| <http://localhost:8000/docs> | Swagger, if you want the API port directly |

Stop it with `make docker-down` (keeps data) or `make docker-nuke` (deletes the
database and cache volumes).

Requires Docker with the Compose v2 plugin, **v2.24 or newer** — the compose file
uses `env_file: required: false`. `docker-compose` (the hyphenated v1 script) is
not supported.

---

## Topology

```
                    ┌───────────────────────────────┐
  browser ────────► │ frontend  nginx :8080         │
                    │  • serves the SPA at /app/    │
                    │  • proxies /api, /console,    │
                    │    /docs, /health → backend   │
                    └──────────────┬────────────────┘
                                   │  /api/v1/...
                    ┌──────────────▼────────────────┐
                    │ backend   uvicorn :8000       │
                    │  • REST API, OpenAPI console  │
                    └───────┬───────────────┬───────┘
                            │               │
                    ┌───────▼──────┐  ┌─────▼──────┐
                    │ postgres :5432│  │ redis :6379│
                    └───────────────┘  └────────────┘
                                    ▲
                    ┌───────────────┴────────────────┐
                    │ migrate  (runs once, exits 0)   │
                    │  alembic upgrade head           │
                    └────────────────────────────────┘
```

`migrate` is the reason a fresh clone works. It runs to completion before the
API is allowed to start (`depends_on: condition: service_completed_successfully`),
so there is no window in which the API is serving requests against a database
with no tables.

Only the web port is published by default in the intended production topology.
`API_PORT` is published too, because the console and the dev vite server are
useful; the production checklist below says what to do about it.

### Why the UI is at `/app/`, not `/`

`frontend/vite.config.ts` compiles production bundles with `base: '/app/'`
(dev builds use `/`). The bundle's asset URLs are therefore absolute under
`/app/`, and the nginx config serves it there.

This is not a preference — `backend/app/main.py` also mounts the same `dist` at
`/app`. The web tier redirects `/` to `/app/` so the first URL someone opens is
the product rather than the API's JSON banner; the console keeps its own path at
`/console`. Changing the bundle base on one side without the other makes every
asset 404. If you move the UI to `/`, change `vite.config.ts`, the
`location /app/` blocks in `frontend/nginx.conf`, **and** the backend's mount
point together.

### Why the API URL is empty at build time

`VITE_API_URL` is compiled into the bundle, and `frontend/src/api/client.ts`
falls back to relative URLs when it is empty. That is the default here, so the
browser calls `/api/v1/...` on its own origin and nginx proxies it upstream —
one origin, no CORS preflight, no second host to keep in sync.

Set `VITE_API_URL` to an absolute URL only when the API really is on another
host. It is a **build** argument: changing it needs
`docker compose build --no-cache frontend`, not a restart. And if you do, that
origin must be in `CORS_ORIGINS`, in the Turnstile site's allowed hostnames, and
in the Google client's authorised JavaScript origins.

---

## Configuration

`.env` at the repo root feeds Compose variable interpolation; every value has a
default, so the stack boots without it. See `.env.example` for the annotated
list. Two things to know:

- `backend/.env` is still read by the API (it holds OAuth, TOTP and webhook
  settings) and is optional. `docker-compose.yml` only sets the
  container-specific keys — `DATABASE_URL`, `REDIS_URL` — because anything in
  compose's `environment` block overrides that file.
- `JWT_SECRET` is deliberately **not** set in `docker-compose.yml`. Compose
  would win over `backend/.env`, silently replacing a real secret. Supply it via
  `backend/.env` or the host environment. If neither provides it, the API falls
  back to the development placeholder compiled into
  `backend/app/config.py` — see the checklist.

Changing `POSTGRES_PASSWORD` after the first `up` does not update the existing
volume: the initialised cluster keeps the original role password and the API
will fail to authenticate. Either keep the defaults or `make docker-nuke` and
start over.

---

## Common tasks

```bash
make docker-lint        # build-context check — no Docker required
make docker-config      # validate the compose file, build nothing
make docker-build       # build both images
make docker-up          # build + start everything
make docker-ps          # status and health
make docker-logs        # follow all logs
make docker-down        # stop, keep data
make docker-nuke        # stop, delete volumes
make docker-migrate     # run alembic in a one-off container
make docker-db          # psql inside the postgres container
make docker-dev         # full stack + vite dev server with HMR on :5174
make docker-worker      # also run the background-job worker
```

`npm run docker:*` mirrors all of these.

`make docker-lint` is the one target worth running on every change, because it is
the only one that needs neither Docker nor a running stack. It walks each service's
build context the way the daemon would — applying the `.dockerignore` of every
directory above the context, honouring re-inclusions, and treating an excluded
directory as excluding its whole subtree — and fails on three things:

- a credential that would be sent to the daemon (`.env`, `*.pem`, `*.key`,
  `*.keystore`, `credentials*.json`, …), reported with the rule that decided it;
- a host build artifact (`node_modules`, `.venv`, `__pycache__`, `.mypy_cache`) that
  would be baked in — a Linux image containing your macOS `node_modules` is a
  silently broken runtime, not just a slow build;
- a `COPY` source that does not exist, which is otherwise only discovered after a
  multi-minute build.

It also enforces a 25MB budget per context, because a regression to 1.7GB (what the
unfiltered tree measured) is otherwise invisible until someone tries to push.

In CI this runs before the image builds, and hadolint covers the Dockerfiles
themselves. The layers-level secret check further down the job is the third layer:
context, syntax, then contents.

The host-based flow is unchanged: `npm run dev` (or `make docker-stores` for
just the data stores, which is what `scripts/dev.sh` starts) runs alembic,
uvicorn and vite on the host.

### Background jobs

`JOBS_ENABLED` defaults to `false`, so the API does not schedule jobs in-process.
Run them separately:

```bash
make docker-worker
```

Turn on **exactly one** of the two. Both running means every scheduled job fires
twice — connector syncs, recalibration and drift checks all duplicating.

### Model serving

The default image installs only the runtime dependencies, so forecasting falls
back to the documented deterministic stub (`backend/app/services/forecasting/bundle.py`)
when no bundle is present. For real LightGBM inference:

```bash
# export a bundle first, if you have not:  make bundle
BACKEND_EXTRAS=model docker compose build backend
docker compose up -d
```

Compose mounts the repo's `model/` directory read-only at `/model` (`*api-common`),
and the loader probes `/model/artifacts/runs/latest/serving_bundle.joblib` by
default — so an exported bundle is picked up with no extra configuration. Set
`MODEL_BUNDLE_PATH` to use a bundle outside the repo. The bundle is written by
`model/scripts/export_serving_bundle.py`. The API logs which it is serving at
boot (`Serving: ...`), and each forecast records it, so a silent fallback is
visible in the data.

The image healthcheck probes `/api/v1/health`, not the bare `/health`. The
liveness endpoint answers 200 whatever forecasting is doing, so a deployment
whose configured bundle stopped resolving used to stay a green container while
every number it returned was a rolling mean. `/api/v1/health` reports that state
as **503 `degraded`**, which marks `backend` unhealthy — and since `frontend`
waits on `backend: condition: service_healthy`, the web tier does not come up
to serve it. `docker compose ps` shows the unhealthy service, and the
`/api/v1/health` response says which mode it is in.

`BACKEND_EXTRAS=ocr` adds pytesseract/Pillow **and** the Tesseract system
packages (`tesseract-ocr`, `tesseract-ocr-eng`, `tesseract-ocr-osd`) to the
runtime image. The Python wrapper alone is not an OCR engine: without the
binary, every photo honestly returns zero rows with a "no engine configured"
note. Set `OCR_LANGUAGE` to match an installed language pack; the default `eng`
is installed above. `OCR_SERVICE_URL` remains the alternative for a hosted
vision service and bypasses the local binary.

Uploads and datasets live in Postgres, not on the container filesystem, so
neither needs a volume.

---

## Production checklist

The defaults are chosen so the stack runs on a laptop with no configuration.
Before exposing it:

- [ ] **`JWT_SECRET` set** to a real secret (`openssl rand -hex 32`) via
      `backend/.env` or the environment. Unset means every deployment shares the
      placeholder and can mint valid tokens for each other.
- [ ] **Stop publishing the API port.** Remove the `ports:` block from the
      `backend` service so the only path in is nginx. Only then flip
      `TRUSTED_PROXY_HEADERS` to `"true"` *and* add `--proxy-headers
      --forwarded-allow-ips=*` to the backend command — a wildcard allow-list
      while `:8000` is still reachable lets any client forge its own
      `X-Forwarded-Proto`.
- [ ] **`CORS_ORIGINS` narrowed** to the real site origin. The default exists for
      the local dev origins.
- [ ] **TLS terminated at the edge.** The stack speaks plain HTTP; put a reverse
      proxy or load balancer in front. The `SameSite`/secure behaviour of the
      auth cookies depends on the request arriving over HTTPS.
- [ ] **Real Postgres credentials** rather than `demandiq/demandiq`, and a
      managed database if you have one — the compose Postgres is a single node
      with no backups.
- [ ] **`APP_ENV=production`** (already the default) and `LOG_LEVEL=INFO`.
- [ ] **Decide on the worker**: `JOBS_ENABLED=true` on the API *or* the worker
      service, never both.
- [ ] **Content-Security-Policy** from the real edge. It is deliberately absent
      from `frontend/nginx.conf`: a correct policy has to allowlist the Turnstile
      challenge host and Google's identity origins, and getting that wrong breaks
      sign-in in a way that is very hard to trace back to a header.
- [ ] **Upload size**: `client_max_body_size 50m` in `frontend/nginx.conf` is
      currently the only limit on the upload path — the backend sets none. Raise
      it and the API together, never one alone.

### Also worth knowing

- Both images run as non-root (uid 1000 for the API, uid 101 for nginx), with
  `no-new-privileges` and `init: true` on the API so uvicorn is reaped properly.
- The web image listens on **8080**, not 80: it cannot bind a privileged port.
- Compose is the only supported topology for the nginx config — it resolves the
  API through Docker's embedded DNS at 127.0.0.11 so it survives the API
  container being recreated with a new IP.
- `postgres` and `redis` publish **no** host ports. 5432 is usually already taken
  on a developer machine, which made the previous compose file fail to start.
  Use `make docker-db`, or uncomment the `ports:` block.

---

## What changed, and why

The repository had three container files, none of which produced a working
deployment.

| Problem | Consequence | Fix |
|---|---|---|
| `backend/Dockerfile` ran `pip install -e .` after copying only `pyproject.toml` | **The image could not build.** `app/` was not present yet, so setuptools' `packages.find` matched nothing | Dependency layer installs the list parsed from `pyproject.toml`, then the code is copied and registered with `--no-deps` |
| No `.dockerignore` anywhere | **`backend/.env` was baked into a layer** — JWT secret, TOTP issuer, DB password — and `docker history` or any registry push leaked them | `backend/.dockerignore`, plus a CI canary that greps the built image for a planted secret |
| `[dev]` extra installed in production | pytest/ruff/black/mypy shipped to runtime | Only the runtime list is installed; `model`/`ocr` are opt-in build args |
| No production frontend image | There was no way to produce a deployable frontend artefact at all | `frontend/Dockerfile`: node build → nginx-unprivileged |
| No migration step in compose | A fresh clone came up with no tables; only `scripts/dev.sh` migrated, on the host | `migrate` service, gated with `service_completed_successfully` |
| No healthchecks on the API or web tier | Startup order was unenforced | Healthchecks in both images, wired through `depends_on`; the API probe uses `/api/v1/health` so a degraded serving mode reads as unhealthy rather than green |
| `version: '3.8'` | Emits an obsolete-key warning on every run | Removed |
| `container_name` on every service | Global names; two checkouts could not coexist; blocked `--scale` | Removed; services are reachable by DNS name |
| Hardcoded `demandiq_password` | One shared secret, in version control | `${POSTGRES_PASSWORD:-demandiq}` |
| `env_file: [./backend/.env]` required | `docker compose` aborted on a clone with no `.env` | `required: false` |
| Postgres published on 5432 | Clashes with a host Postgres | Not published |
| `postgresql-client` in the runtime image | ~50MB for tooling nothing in the image uses (`psql` is only used by the host-side E2E scripts) | Dropped; the build stage is separate anyway |
| Unfiltered root build context | **~1.7GB** sent to the daemon on every frontend build | Root `.dockerignore`; measured **~2.3MB** |
| No CI coverage of the images | A Dockerfile could rot until someone tried to deploy | `docker` job builds both, boots the stack, probes it, and checks for secret leakage |

The one-line summary: the images were not merely un-hardened, they were
non-functional — the API image could not build, and the secret that would have
leaked was one `COPY . .` away from being published.

---

## Verification status

**The images were never built or run.** Docker is not installed in the
environment where this container work was done, so no `docker build`,
`docker compose up`, or image healthcheck has been executed. Treat the first
real build as unverified.

What *was* checked statically:

- `docker-compose.yml` parses, and the YAML merge keys resolve to a well-formed
  config for every service (correct `environment` maps, `env_file` shapes,
  `depends_on` conditions, profiles).
- The dependency-parsing one-liner in `backend/Dockerfile` produces the correct
  16 runtime requirements, and 21 with `INSTALL_EXTRAS="model ocr"` — no
  `[dev]` leakage.
- `npm ci` succeeds from a context containing only `package.json`,
  `package-lock.json` and the two workspace manifests (505 packages), which is
  what `frontend/Dockerfile` relies on.
- The three `.dockerignore` files are now enforced by
  `scripts/check_docker_context.py` rather than checked once by hand: 565 files
  across the three contexts, no credential and no host artifact in any of them,
  every `COPY` source resolves.
- Measured context sizes, as the linter counts them: **1.26MB** (API, 146 files),
  **2.19MB** (web, 214 files), **1.92MB** (dev web, 205 files) — from 1.7GB
  unfiltered.
- The linter is itself tested against planted leaks: an uncovered credential type
  and a mistyped `COPY` both fail it, while a `backend/.env` and a root `.env` do
  not, because the ignore rules exclude them. That asymmetry is the point.
- `make -n` on every new target; `make help` lists them; `package.json` parses.
- `.github/workflows/ci.yml` parses and the new job's steps are in order.

The `docker` CI job is the real gate: it builds both images, boots the stack,
migrates a fresh database, probes the SPA, a hashed asset, a deep link and the
API through the proxy, and fails if a planted secret reaches an image layer. Run
it before trusting any of this.

---

## Troubleshooting

**`migrate` fails with `relation "workspaces" already exists`.**
Something created the schema outside Alembic. The compose Postgres deliberately
does *not* mount `database/*.sql` into `/docker-entrypoint-initdb.d` — it used
to, which is exactly how this happened. Check for a stray mount, then
`make docker-nuke` and retry.

**The page loads but every asset 404s.**
The bundle was built with a different base than it is being served from. Check
that `vite.config.ts`'s `base: '/app/'` and the `location /app/` blocks in
`frontend/nginx.conf` agree.

**Uploads fail with 413.**
`client_max_body_size` (50m) in `frontend/nginx.conf`. Raise it there; the
backend applies no limit of its own.

**`port is already allocated`.**
Something else holds 5173 or 8000. Set `WEB_PORT` / `API_PORT` in `.env`, or stop
the other process — a host `npm run dev` will conflict with the containerised
web tier on 5173.

**The API is healthy but the UI cannot sign in.**
Check the browser console for a CORS error. Same-origin is the default; if
`VITE_API_URL` is set to an absolute URL, that origin must also be in
`CORS_ORIGINS` and in the Google client's authorised origins.

**Forecasts look like the stub.**
Expected without `BACKEND_EXTRAS=model` and a mounted bundle. The API logs
`Serving: ...` at boot — check `make docker-logs`. `GET /api/v1/health` says
the same thing as data (`serving.mode`), and the Model Registry's *Serving now*
panel shows it in the product UI.

**The API refuses to start with a `ServingConfigurationError`.**
`MODEL_BUNDLE_PATH` is set but nothing loadable is at that path. That is
intentional: an explicit path means this deployment serves the trained model,
and booting into the stub anyway would look healthy while every forecast was a
rolling mean. Fix the path (or, if you meant to run the stub, unset `MODEL_BUNDLE_PATH`
— in this stack the bundle is found through the `/model` mount with no variable
set). `GET /api/v1/health` reports the same condition as `503 status: degraded`
with `serving.mode: error`.

**`backend` is `unhealthy` but the API answers requests.**
The image healthcheck probes `/api/v1/health`, so an unhealthy backend is
usually a serving-mode error rather than a dead process: `MODEL_BUNDLE_PATH`
points at something that no longer loads. This is the case the startup check
cannot cover — a process that booted with a working bundle and lost it keeps
serving — so the probe catches it and `frontend` stays down rather than showing
the stub's numbers. Check `curl -s localhost:8000/api/v1/health | grep serving`
and the `make docker-logs` boot line.
