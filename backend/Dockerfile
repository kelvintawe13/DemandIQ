# syntax=docker/dockerfile:1
#
# DemandIQ backend — production image.
#
# What was wrong with the previous version, and why this one is shaped as it is:
#
#   1. It could not build. `COPY pyproject.toml ./` followed by
#      `pip install -e ".[dev]"` asks setuptools to editable-install a package
#      it cannot see: `app/` is not in the image yet, and pyproject's
#      `packages.find include = ["app*"]` therefore matches nothing.
#   2. It installed the [dev] extra (pytest/ruff/black/mypy) into a production
#      image, editable, so the shipped code was whatever the next layer left
#      behind.
#   3. `COPY . .` had no .dockerignore to honour, so backend/.env — JWT_SECRET,
#      the TOTP issuer, the database password — was baked into a layer. See
#      backend/.dockerignore, which is what actually prevents that; .gitignore
#      has no effect on a Docker build context.
#
# Shape: a builder stage compiles dependencies into /opt/venv, and the runtime
# stage copies that venv. The runtime therefore carries no compiler at all,
# without the `apt-get purge build-essential` dance that risks removing a
# libstdc++/libgomp a compiled wheel still needs at runtime.

# ---------------------------------------------------------------------------
# 3.11 is a hard floor, not a preference: the code uses `X | None` unions at
# runtime, which 3.10 evaluates differently.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# Keep the image honest about what it needs. `model` adds lightgbm/pandas/numpy
# for real LightGBM inference; the forecasting bundle loader falls back to stub
# forecasts when MODEL_BUNDLE_PATH is unset (app/services/forecasting/bundle.py),
# so the default image runs without it. Pass extras as a space-separated list:
#   --build-arg INSTALL_EXTRAS="model ocr"
ARG INSTALL_EXTRAS=""
ARG PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential is only needed if a dependency compiles from source, and only
# in this stage. libpq5 is deliberately absent: psycopg2-binary ships its own,
# and the only `psql` callers are backend/scripts/*_e2e.sh, which boot a
# throwaway cluster on the host and never run inside this image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Every copy and install below is relative to this directory. Without it the
# manifests land in `/` and `pip install .` runs there, so setuptools' package
# discovery walks the entire container filesystem (including /proc) and hangs
# instead of building the wheel.
WORKDIR /app

# The dependency layer copies ONLY pyproject.toml/README.md and installs the
# runtime dependency list parsed out of it, so editing application code does not
# invalidate the slow layer above. Reading the list out of pyproject rather than
# keeping a second requirements.txt removes the drift risk between the two.
COPY pyproject.toml README.md ./
RUN set -eux; \
    python -c "import tomllib,pathlib;d=tomllib.loads(pathlib.Path('pyproject.toml').read_text());deps=list(d['project']['dependencies']);extras='${INSTALL_EXTRAS}'.split();[deps.extend(d['project']['optional-dependencies'][e]) for e in extras if e];pathlib.Path('/tmp/reqs.txt').write_text('\n'.join(deps)+'\n')"; \
    pip install --no-cache-dir -r /tmp/reqs.txt; \
    rm -f /tmp/reqs.txt

# Application code last, so the dependency layer above stays cached.
#
# `COPY . .` is safe only because backend/.dockerignore strips .env, caches,
# venvs and tests first.
COPY . .

# Register the project's own metadata (no dependency re-resolution — that already
# happened above) so `pip show demandiq-backend` works and the package imports
# from any working directory, not just /app. This also fails the build early if
# pyproject's package discovery is broken, rather than at first request.
RUN pip install --no-cache-dir --no-deps --no-build-isolation . \
    && find /app -name '__pycache__' -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# The Python OCR extra installs `pytesseract`, but that package is only a
# wrapper: the Tesseract executable and its language data are OS packages.
# Install both when the same build arg asks for OCR, so `BACKEND_EXTRAS=ocr`
# actually produces a working local engine instead of an ImportError at the
# first photo. The language pack is explicit (`eng`) because Debian's
# `tesseract-ocr` package does not guarantee English data is pulled in.
ARG INSTALL_EXTRAS=""
RUN case " ${INSTALL_EXTRAS} " in \
        *" ocr "*) \
            apt-get update \
            && apt-get install -y --no-install-recommends \
                tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd \
            && rm -rf /var/lib/apt/lists/* \
            ;; \
    esac

# Refuse to buffer or write .pyc files: logs must reach `docker logs`
# unbuffered, and a read-only-ish app dir should not accumulate bytecode.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

# uid/gid 1000 matches the usual first local user, so a bind-mounted source tree
# in the dev override lines up with host ownership instead of producing
# root-owned files the host user then cannot edit.
RUN groupadd --gid 1000 demandiq \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin demandiq
USER 1000:1000

EXPOSE 8000

# stdlib rather than curl: python:slim ships no curl, and adding one just to
# service a healthcheck is another package to keep patched.
#
# `/api/v1/health`, not `/health`. The liveness endpoint answers 200
# unconditionally, so a deployment whose MODEL_BUNDLE_PATH was set and then
# stopped resolving stayed `healthy` while every number it returned was a
# rolling-mean stub — the silent fallback the startup check exists to prevent,
# surviving as a green container. The detailed endpoint reports that state as
# 503 `degraded` (see app/routers/health.py), so this probe fails with it.
#
# It deliberately does NOT fail on a missing database: `/api/v1/health` reports
# `database: unavailable` in the body but only the serving mode moves the status
# code, which keeps this a serving-correctness gate rather than a second,
# flappier database probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

# No --reload (that is the dev server's job) and no --workers (scale with
# replicas, not threads).
#
# Behind the compose nginx this app also needs
# `--proxy-headers --forwarded-allow-ips='*'` for X-Forwarded-Proto to arrive,
# but only when that proxy is the sole path in. A wildcard allow-list on a
# directly published port lets a client forge its own scheme, so it is
# deliberately not baked into the image — see DOCKER.md and the compose
# `proxy-headers` profile.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
