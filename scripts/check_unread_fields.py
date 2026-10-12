#!/usr/bin/env python3
"""Backend response keys that no frontend screen reads.

A key the API sends and nothing renders is either a screen that forgot to show
it (the waste panel read ``waste.observations`` while the router sent
``descriptive_stats``, and answered with a literal baked into the component) or
API surface that is deliberately unwired. Neither is visible from either side
alone, so this reads the response dict literals out of ``backend/app/routers``
and checks each key against ``frontend/src``.

Exits non-zero for both failure modes:

* an unread key that is not in :data:`REVIEWED_UNREAD` — wire it into a screen,
  drop it from the response, or add it below with the reason it stays unwired;
* a ``REVIEWED_UNREAD`` entry nothing reports any more — delete the entry, so
  the list cannot rot into a blanket exemption.

Run it from anywhere: ``python3 scripts/check_unread_fields.py``. No third-party
imports, so it needs no venv and no install.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTERS = ROOT / "backend" / "app" / "routers"
FRONTEND = ROOT / "frontend" / "src"

KEY = re.compile(r'"([a-z][a-z0-9_]*)":')
# ``text("… WHERE user_id=:uid")`` — a bind parameter, not a response field.
PLACEHOLDER = re.compile(r":([a-z][a-z0-9_]*)")
# How a handler hands a payload back: a returned dict, a returned list of row
# dicts, or a row appended to a list that is then returned.
PAYLOAD = re.compile(r"\breturn (?P<returned>[{\[])|\.append\(\s*(?P<appended>[{\[])")

# Keys that are deliberately returned and deliberately unrendered, with the
# reason. Kept as short as it can honestly be: every response key the routers
# build reaches a screen unless the client is not a screen at all. The entries
# that used to sit here were wired up (the grant's expiry, the model a rollback
# replaced, the webhook signing header, the admin reset's confirmation) or
# dropped from the response as a duplicate, a constant or an always-null field
# (`protocol`, `direction`, `ended_at`, `rerun_of`, `experiment_id`,
# `provider_uid`).
#
# Add a key here only with the reason it stays unwired, and expect the reason to
# be argued with.
REVIEWED_UNREAD: dict[str, str] = {
    # The realtime WebSocket frame's topic discriminator, consumed by the
    # restaurant device agent rather than by any screen. It is the one field on
    # the frame that cannot be dropped: a client has to know which topic a
    # message belongs to before it can route it, and the four topics travel the
    # same socket. Rendering it in the console would be wrong rather than
    # merely redundant — the console is not the audience for a device feed.
    "stream.py:topic": "read by the device agent over the WebSocket, not by a screen",
    # Nested inside the waste summary's ``descriptive_stats``, which
    # ``DatasetsAdmin.tsx`` does read as a whole (``waste?.descriptive_stats``)
    # — the panel renders the headline figures and the per-establishment spread
    # (``establishment_variation``) from that object, not this monthly series.
    # The field is the waste-seasonality evidence (Section 3.21: March waste ran
    # about twice November's), which is why the endpoint computes it; the panel
    # is not its audience. This checker matches key literals against the
    # frontend, so a member of an object a screen reads whole looks unread.
    "datasets.py:monthly_pattern": "member of descriptive_stats, which the datasets panel reads whole",
    # The three forecast-scoped endpoints — /forecasts/{id}/explanation/summary,
    # /policy/simulation and /drift/monitoring — are API contract, reimplemented
    # rather than deleted (decided deliberately; they are documented in
    # API_ENDPOINTS.md). No screen consumes them yet: the forecast screen renders
    # the per-feature attribution (ForecastWhy.tsx), not the plain-language
    # summary, the per-day policy trade-off or the drift PSI. So every key below
    # is unrendered by construction rather than forgotten, and each belongs to
    # one endpoint, noted here so the reason stays checkable. Wire a screen and
    # the checker will fail on the stale entry until it is deleted.
    "forecasts.py:dominant_factor": "explanation/summary — no screen consumes the endpoint",
    "forecasts.py:negative_factors": "explanation/summary — no screen consumes the endpoint",
    "forecasts.py:positive_factors": "explanation/summary — no screen consumes the endpoint",
    "forecasts.py:residual_visitors": "explanation/summary — no screen consumes the endpoint",
    "forecasts.py:total_drivers": "explanation/summary — no screen consumes the endpoint",
    "forecasts.py:total_impact_visitors": "explanation/summary — no screen consumes the endpoint",
    "forecasts.py:lowest_realised_surplus": "policy/simulation — no screen consumes the endpoint",
    "forecasts.py:lowest_unmet_demand": "policy/simulation — no screen consumes the endpoint",
    "forecasts.py:baseline_count": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:baseline_days": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:baseline_from": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:baseline_kind": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:baseline_to": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:current_psi": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:drift_reason": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:drift_status": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:model_training_window": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:psi": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:psi_threshold": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:recent_count": "drift/monitoring — no screen consumes the endpoint",
    "forecasts.py:recent_days": "drift/monitoring — no screen consumes the endpoint",
}


def _string_end(text: str, start: int) -> int:
    """Index just past the string literal that starts at ``start``."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return len(text)


def returned_keys(text: str) -> set[str]:
    """Keys of the payload literals this module builds.

    SQL bind-parameter blocks are dict literals too — ``{"wid": …, "uid": …}``
    inside ``db.execute(text(…))`` — and are subtracted by name, since they
    appear as ``:wid``/``:uid`` placeholders in the same file.

    The limit of this: a payload assembled into a model rather than a literal
    (``Upload(preview={"all_rows": …})``, handed back by another handler) is
    invisible here. The report is a shortlist to review, not a proof.
    """
    keys: set[str] = set()
    for match in PAYLOAD.finditer(text):
        opener = match.end() - 1
        i, depth = opener, 0
        while i < len(text):
            c = text[i]
            if c in "\"'":
                i = _string_end(text, i)
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        keys |= set(KEY.findall(text[opener : i + 1]))
    return keys - set(PLACEHOLDER.findall(text))


def main() -> int:
    sources = [
        p
        for p in FRONTEND.rglob("*")
        if p.suffix in {".ts", ".tsx"} and p.is_file()
    ]
    if not sources:
        print(f"no frontend sources under {FRONTEND}", file=sys.stderr)
        return 2
    frontend = "\n".join(p.read_text(errors="ignore") for p in sources)

    unread: set[str] = set()
    for router in sorted(ROUTERS.glob("*.py")):
        for key in returned_keys(router.read_text()):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", frontend):
                continue
            unread.add(f"{router.name}:{key}")

    unreviewed = sorted(k for k in unread if k not in REVIEWED_UNREAD)
    stale = sorted(k for k in REVIEWED_UNREAD if k not in unread)

    for entry in sorted(unread):
        reason = REVIEWED_UNREAD.get(entry, "NO REASON RECORDED")
        print(f"  unread  {entry:48s} {reason}")
    for entry in stale:
        key = entry.split(":", 1)[1]
        reads = re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", frontend)
        why = "a screen reads this key now" if reads else "no response sends this key any more"
        print(f"  stale   {entry:48s} {why} — delete the entry")

    if unreviewed:
        print(
            f"\n{len(unreviewed)} response key(s) no screen reads and no reason records:",
            file=sys.stderr,
        )
        for entry in sorted(unreviewed):
            print(f"  {entry}", file=sys.stderr)
        print(
            "\nRender it, drop it from the response, or add it to REVIEWED_UNREAD "
            "in this script with the reason it stays unwired.",
            file=sys.stderr,
        )
        return 1
    if stale:
        print(
            f"\n{len(stale)} REVIEWED_UNREAD entry(ies) are no longer unread. Delete "
            "those lines from this script.",
            file=sys.stderr,
        )
        return 1

    if not unread:
        print("\nNo unreviewed keys and no exceptions: every response key reaches a screen.")
        return 0
    print(f"\n{len(unread)} reviewed unread key(s); every other response key reaches a screen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
