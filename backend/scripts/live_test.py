#!/usr/bin/env python3.11
"""Live test — hits every openapi path on a running server until all are valid.

Senior: treats 401/403/422/404 as *valid* (auth/validation), only 500+ is failure.
Checks index.html via GET / (html) and GET /console (html) and GET /app (frontend).
"""

import sys

import httpx

BASE = "http://127.0.0.1:8000"


def check():
    # Load openapi via live server
    r = httpx.get(f"{BASE}/openapi.json", timeout=5)
    r.raise_for_status()
    openapi = r.json()
    paths = openapi["paths"]
    print(f"Live openapi: {len(paths)} paths")
    ok = 0
    fail = 0
    results = []
    for path, methods in sorted(paths.items()):
        for method in methods:
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            url = BASE + path
            # Replace path params with dummy UUIDs
            import re

            # Find {param} and replace
            if "{" in path:
                # use a fixed UUID for location_id etc
                dummy = "00000000-0000-0000-0000-000000000001"
                url = re.sub(r"\{[^}]+\}", dummy, url)
                # For forecast export, add query
                if "export" in path:
                    url += "?format=csv"
            # For paths with query required, openapi may have required query params; we just hit bare path and expect 422
            try:
                resp = httpx.request(
                    method.upper(), url, timeout=5, headers={"Accept": "application/json"}
                )
                status = resp.status_code
                # Valid statuses: 200,201,202,204,400,401,403,404,409,422,429,405
                valid = status < 500
                # Special: 500 is fail, also check for Pydantic not defined
                if "not fully defined" in resp.text:
                    valid = False
                prefix = "✓" if valid else "✗"
                results.append(
                    (f"{method.upper():6} {path:45} → {status} {resp.reason_phrase[:20]}", valid)
                )
                if valid:
                    ok += 1
                else:
                    fail += 1
                    print(f"{prefix} {method.upper():6} {path} → {status} FAILED")
                    print(resp.text[:300])
            except Exception as e:
                fail += 1
                results.append((f"{method.upper():6} {path} → EXC {e}", False))
                print(f"✗ {method.upper()} {path} EXC {e}")

    print(f"\nLive test: {ok} ok, {fail} failed out of {ok+fail}")
    # Index.html checks
    print("\n--- index.html checks ---")
    checks = [
        ("GET / (json)", httpx.get(f"{BASE}/", headers={"Accept": "application/json"})),
        ("GET / (html)", httpx.get(f"{BASE}/", headers={"Accept": "text/html"})),
        ("GET /console", httpx.get(f"{BASE}/console")),
        ("GET /docs", httpx.get(f"{BASE}/docs")),
        ("GET /health", httpx.get(f"{BASE}/health")),
        ("GET /app", httpx.get(f"{BASE}/app", headers={"Accept": "text/html"})),
        ("GET /app/index.html", httpx.get(f"{BASE}/app/index.html")),
        # A deep link into the shell: the mount must fall back to index.html
        # rather than 404 (the app is built with `base: '/app/'`).
        ("GET /app/login (html)", httpx.get(f"{BASE}/app/login", headers={"Accept": "text/html"})),
    ]
    for label, resp in checks:
        ct = resp.headers.get("content-type", "")
        ok2 = resp.status_code == 200 and ("html" in ct or "json" in ct or "text" in ct)
        mark = "✓" if ok2 else "✗"
        print(f"{mark} {label}: {resp.status_code} {ct[:30]} {len(resp.text)}B")
        if "html" in label.lower() and "html" not in ct:
            print(f"  HTML check failed for {label}")
            fail += 1
        # For /console, ensure CATALOG present
        if label == "GET /console":
            if "CATALOG" not in resp.text:
                print("  CATALOG missing in /console")
                fail += 1
            else:
                print(f"  CATALOG entries: {resp.text.count('/api/v1')}")
    # Summary
    print(f"\nTotal endpoints tested: {ok+fail - 7 + 7}")  # workaround

    # Print sorted results for report
    print("\n--- All endpoints (sorted) ---")
    for line, valid in sorted(results):
        mark = "✓" if valid else "✗"
        print(f"{mark} {line}")

    if fail == 0:
        print(f"\nAll endpoints valid — we have worked nicely ✓ ( {ok} routes, no 500 )")
        return 0
    else:
        print(f"\n{fail} endpoints invalid — needs fix")
        return 1


if __name__ == "__main__":
    sys.exit(check())
