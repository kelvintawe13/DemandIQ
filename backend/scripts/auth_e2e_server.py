#!/usr/bin/env python3
"""Auth E2E server launcher (real FastAPI app + optional JWKS override).

Used by scripts/auth_e2e.sh; env:
  DATABASE_URL  - target Postgres (required)
  PORT          - bind port (default 8011)
  JWKS_URL      - override Google JWKS endpoint (local JWKS server for tests)
"""

from __future__ import annotations

import os

import uvicorn

from app.security import human

if os.environ.get("JWKS_URL"):
    human._GOOGLE_JWKS_URL = os.environ["JWKS_URL"]

from app.main import create_app

uvicorn.run(
    create_app(),
    host="127.0.0.1",
    port=int(os.environ.get("PORT", "8011")),
    log_level="warning",
)
