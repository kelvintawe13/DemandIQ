#!/usr/bin/env python3.11
"""Regenerate api-console.html CATALOG from live openapi.json.

Senior: deterministic, no manual drift. Run after any router change:
  python3.11 backend/scripts/sync_console.py
Or via npm: npm run sync:console
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402

app = create_app()
openapi = app.openapi()

group_map = {
    "ops": ("Infra", "Platform", False),
    "auth": ("Auth", "Identity", False),
    "locations": ("Locations", "Workspace", False),
    "forecasts": ("Forecasting", "Predictions", False),
    "policies": ("Policies", "Decisions", False),
    "quality": ("Quality", "Trust", False),
    "alerts": ("Quality", "Trust", False),
    "ingestion": ("Ingestion", "Data in", False),
    "governance": ("Governance", "Admin", True),
    "reports": ("Analytics", "Reporting", False),
    "webhooks": ("Webhooks", "Admin", True),
    "analytics": ("Analytics", "Reporting", False),
    "models": ("Models", "Admin", True),
    "experiments": ("Models", "Admin", True),
    "features": ("Models", "Admin", True),
    "datasets": ("Governance", "Admin", True),
    "sources": ("Sources", "Channels", False),
    "connectors": ("Sources", "Channels", False),
    "me": ("Account", "Me", False),
    "menu": ("Locations", "Workspace", False),
    "access": ("Governance", "Admin", True),
}


def fake_mock(path: str, method: str, op: dict) -> dict:
    # Compact domain mocks — contract-accurate shapes
    if path in ("/health", "/api/v1/health"):
        return {"status": "ok", "database": "ok", "redis": "ok", "version": "0.9.0"}
    if path.startswith("/api/v1/auth"):
        if "register" in path:
            return {"id": "usr_01HZ", "workspace_id": "wsp_01HZ", "verification_sent": True}
        if "login" in path:
            return {"access_token": "eyJ...", "refresh_token": "rt_...", "token_type": "bearer"}
        return {"status": "ok"}
    if path.startswith("/api/v1/me"):
        if "profile" in path:
            return {
                "id": "usr_01HZ",
                "name": "Aline",
                "email": "aline@demandiq.app",
                "phone": "+250788000001",
            }
        if "preferences" in path:
            return {"theme": "light", "language": "en"}
        return {"status": "ok"}
    if path.startswith("/api/v1/ingest"):
        if "manual" in path:
            return {"committed": 3, "total": 3}
        if "file" in path and method == "post" and "{upload_id}" not in path:
            return {"upload_id": "upl_2231", "status": "awaiting_mapping"}
        return {"status": "ok"}
    if path.startswith("/api/v1/locations"):
        return {"data": [{"id": "loc_kim", "name": "Kimironko Kitchen"}]}
    if path.startswith("/api/v1/forecasts"):
        if method == "post" and "batch" not in path:
            return {
                "id": "fc_8812",
                "point": 164,
                "lower": 141,
                "upper": 189,
                "coverage_target": 0.9,
            }
        return {"data": []}
    if path.startswith("/api/v1/policies") and "simulate" in path:
        return {"results": [{"policy": "uncertainty_aware", "unmet_demand": 96}]}
    if path.startswith("/api/v1/quality") or path.startswith("/api/v1/alerts"):
        return {"data": []}
    if path.startswith("/api/v1/analytics"):
        return {"data": []}
    if path.startswith("/api/v1/webhooks"):
        return {"data": []}
    return {"status": "ok", "_path": path}


catalog = defaultdict(lambda: {"group": None, "tag": None, "admin": False, "items": []})

for path, methods in openapi["paths"].items():
    for method, op in methods.items():
        if method not in ("get", "post", "put", "patch", "delete"):
            continue
        tags = op.get("tags", ["ops"])
        tag = tags[0] if tags else "ops"
        group, label, admin = group_map.get(tag, (tag.capitalize(), tag.capitalize(), False))
        key = (group, label, admin)
        if catalog[key]["group"] is None:
            catalog[key]["group"] = group
            catalog[key]["tag"] = label
            catalog[key]["admin"] = admin
        mock = fake_mock(path, method, op)
        body = None
        if "requestBody" in op and "application/json" in op["requestBody"].get("content", {}):
            try:
                body = op["requestBody"]["content"]["application/json"].get("example")
            except Exception:
                body = None
        if body is None and "ingest/manual" in path:
            body = [{"location_id": "loc_kim", "date": "2026-09-18", "demand_qty": 168}]
        t = (
            (op.get("summary") or op.get("operationId") or path.split("/")[-1] or tag)
            .replace("_", " ")
            .title()
        )
        item: dict = {"m": method.upper(), "p": path, "t": t, "mock": mock}
        if body is not None:
            item["body"] = body
        if admin:
            item["admin"] = True
        catalog[key]["items"].append(item)

result = []
for key in sorted(catalog.keys()):
    e = catalog[key]
    e["items"] = sorted(e["items"], key=lambda x: x["p"])
    result.append({"group": e["group"], "tag": e["tag"], "admin": e["admin"], "items": e["items"]})

js = json.dumps(result, indent=2)
html = pathlib.Path(ROOT / "api-console.html").read_text()
new = re.sub(
    r"const CATALOG = \[.*?\];\s*\n\n/\* ===========================================================================",
    f"const CATALOG = {js};\n\n/* ===========================================================================",
    html,
    flags=re.DOTALL,
    count=1,
)
pathlib.Path(ROOT / "api-console.html").write_text(new)
print(
    f"Synced catalog: {len(openapi['paths'])} paths → {sum(len(g['items']) for g in result)} items → api-console.html"
)
