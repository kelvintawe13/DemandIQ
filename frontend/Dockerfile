# syntax=docker/dockerfile:1
#
# DemandIQ frontend — production image: static bundle served by nginx.
#
# Build context is the REPOSITORY ROOT, not frontend/. frontend/ has no
# package-lock.json of its own — dependencies are an npm workspace hoisted to
# the root lockfile — so a frontend-only context cannot install reproducibly.
# .dockerignore at the root keeps that context to a few MB; unfiltered it is
# ~1.7GB (model/ alone is 943M).
#
# The previous state was frontend/Dockerfile.dev only: a vite dev server with
# hot reload and no build. There was no way to produce a deployable frontend
# artefact from this repo at all.

# ---------------------------------------------------------------------------
# Build stage — throwaway, so it can be as heavy as it likes.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /src

# `playwright` is a frontend devDependency, so `npm ci` would run its
# postinstall and download ~500MB of browser binaries into the layer. Nothing in
# a bundle build needs them, and CI already installs browsers separately for the
# E2E suites.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Manifests first, and each workspace's package.json, so `npm ci` resolves
# before any source is copied. `npm ci` fails outright if a workspace declared
# in the root package.json is missing its manifest.
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
COPY shared/package.json ./shared/package.json

# `npm ci`, not `npm install`: it installs exactly the lockfile and fails rather
# than silently resolving something new — the same rule the CI frontend job and
# `make install-frontend` follow.
RUN npm ci --no-audit --no-fund

COPY shared ./shared
COPY frontend ./frontend

# ---------------------------------------------------------------------------
# Build-time configuration.
#
# VITE_API_URL is compiled into the bundle, not read at runtime. The default is
# deliberately EMPTY: frontend/src/api/client.ts then falls back to relative
# URLs, so the SPA calls /api/v1/... on its own origin and the compose nginx
# proxies it upstream. That keeps the browser same-origin, so there is no CORS
# preflight and no second origin to keep in sync.
#
# Set it to an absolute URL only when the API is genuinely on another host (a
# CDN-fronted API, say) — and then that origin must be in the backend's
# CORS_ORIGINS, and the Turnstile/Google origins in those dashboards must
# include the site's origin too.
# ---------------------------------------------------------------------------
ARG VITE_API_URL=""
ARG VITE_TURNSTILE_SITE_KEY=""
ARG VITE_GOOGLE_CLIENT_ID=""

# The commit this bundle came from, stamped in the footer (`src/lib/build.ts`).
#
# There is no `.git` in the build context, so vite.config.ts cannot look it up
# from inside the image. `GIT_SHA` is supplied by `make docker-build` (compose
# cannot run git itself); `RAILWAY_GIT_COMMIT_SHA` is a Railway-provided
# variable the platform passes as a build arg, so the deployed footer names the
# commit with nothing to configure. Both may be empty: the stamp then falls back
# to a build timestamp, which still tells two images apart.
ARG GIT_SHA=""
ARG RAILWAY_GIT_COMMIT_SHA=""

ENV VITE_API_URL=${VITE_API_URL} \
    VITE_TURNSTILE_SITE_KEY=${VITE_TURNSTILE_SITE_KEY} \
    VITE_GOOGLE_CLIENT_ID=${VITE_GOOGLE_CLIENT_ID} \
    GIT_SHA=${GIT_SHA} \
    RAILWAY_GIT_COMMIT_SHA=${RAILWAY_GIT_COMMIT_SHA}

# vite.config.ts pins `base` to '/app/' for production builds, so the bundle
# expects to be served under /app/. The nginx config serves it there, and the
# backend mounts the same dist at /app when it serves the SPA itself — do not
# "fix" one side without the other or every asset URL 404s.
RUN npm run build --workspace=frontend

# ---------------------------------------------------------------------------
# Runtime stage — nginx, unprivileged.
# ---------------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

LABEL org.opencontainers.image.title="DemandIQ frontend" \
      org.opencontainers.image.description="Static SPA bundle + reverse proxy to the DemandIQ API"

# The SPA lives under /app/ to match the bundle's compiled base path.
COPY --from=build /src/frontend/dist /usr/share/nginx/html/app
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# 8080, not 80: this image runs as uid 101 and cannot bind a privileged port.
EXPOSE 8080

# Reports the container's own liveness only. Whether the API is reachable is the
# backend's healthcheck and the compose dependency chain to answer — a health
# probe that fails on an upstream outage restarts the one container that is
# still fine.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]

# The base image already runs as the unprivileged `nginx` user and ships a
# SIGQUIT STOPSIGNAL for graceful shutdown; compose pairs that with
# stop_grace_period so in-flight requests finish.
