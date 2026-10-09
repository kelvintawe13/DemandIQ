#!/usr/bin/env python3
"""Replace the demo workspace's placeholder demand with a real ~2-month sample.

Why this exists
---------------
``seed_demo_data.py`` fills the *furniture* surfaces (reports, devices, webhooks,
policies, ...) but it never seeded history: the workspace carried a few dozen
``demand_observations`` and a handful of forecasts, so the demand-facing screens
(Overview, Analytics, Demand forecast, Forecast accuracy, Quality monitor) were
thin enough to read as "not built". This script replaces that placeholder with a
genuine sample drawn from the Recruit Restaurant Visitor Forecasting dataset
(``model/data/recruit``) — the same public data the production model was trained
on — and then runs the real serving path so every number on screen is model
output, not a jittered stub.

What it does, in order
----------------------
1. **Clears** the workspace's demand-derived rows (forecasts, explanations,
   observations, reservations, ingest uploads, quality issues and alerts) so a
   re-run is a refresh, not an append.
2. **Ingests** ~2 months of real daily visitor counts and reservation rows for
   three locations, shifted onto the trailing window that ends yesterday. The
   real series is used verbatim; only the calendar is rebased so that "last 7
   days" / "last 30 days" screens resolve to real history.
3. **Generates** real forecasts through
   :func:`app.services.forecasting.generate.generate_forecast` for the recent
   past (so Forecast accuracy has published forecasts to score, leakage-safe via
   each forecast's ``as_of``) and for the upcoming horizon (the live forecast).
4. **Derives** quality issues (spikes, gaps) and alerts (forecast error, interval
   coverage, drift) from the freshly loaded data instead of hardcoding them.

Idempotent in effect: the clear step makes the workspace state a pure function of
the source CSVs plus this run, so re-running reproduces the same demo.

Usage:
    backend/.venv/bin/python backend/scripts/seed_real_demand.py
    backend/.venv/bin/python backend/scripts/seed_real_demand.py --history-days 63 --horizon 16
    backend/.venv/bin/python backend/scripts/seed_real_demand.py --keep-alerts --keep-quality
"""

from __future__ import annotations

import argparse
import csv
import datetime
import statistics
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session_factory
from app.services.accuracy import compute_accuracy
from app.services.forecasting.generate import generate_forecast

# ---------------------------------------------------------------------------
# Source data and the location mapping
# ---------------------------------------------------------------------------

# The workspace ``seed_demo_roles.py`` / ``seed_demo_data.py`` build.
DEFAULT_WORKSPACE_HINT = "DemandIQ Demo"

REPO_ROOT = Path(__file__).resolve().parents[2]
VISIT_CSV = REPO_ROOT / "model" / "data" / "recruit" / "air_visit_data.csv"
RESERVE_CSV = REPO_ROOT / "model" / "data" / "recruit" / "air_reserve.csv"

# Real stores from the dataset, chosen for (a) full daily coverage across the
# source window, so no day is invented, and (b) distinct volume profiles — a
# busy venue and a smaller one — so the by-location comparison has contrast.
# They are ordinary stores, not outliers, and the accuracy the model shows on
# them is its own, measured rather than assumed.
#   location name            -> air_store_id
STORE_FOR_LOCATION: dict[str, str] = {
    "Kigali Heights Bistro": "air_64a5d5c1381837af",  # ~40 covers/day
    "Remera Grill": "air_4d71826793c09b22",  # ~23 covers/day
}

# Year the real series is drawn from. The rebase below keeps the same calendar
# month and weekday as the target window so the model's calendar features (dow,
# month, week-of-year, holidays) stay in-distribution; only the year moves.
SOURCE_YEAR = 2016

DEFAULT_HISTORY_DAYS = 62  # ~two months
DEFAULT_FORECAST_HISTORY_DAYS = 35  # enough for the 28-day accuracy window + drift
DEFAULT_HORIZON = 16
COVERAGE_TARGET = 90.0

# Quality rules the derived issues are filed against (seeded by seed_demo_data).
RULE_SPIKE = "spike_detection"
RULE_GAP = "gap_detection"
# Alert rules the derived alerts are filed against.
RULE_FORECAST_ERROR = "forecast_error"
RULE_COVERAGE = "coverage_breach"
RULE_DRIFT = "drift"

# A day is an implausible spike at 3x the window median — the same threshold the
# quality monitor's copy describes.
SPIKE_RATIO = 3.0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _rows(conn: sa.Connection, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    return list(conn.execute(sa.text(sql), params or {}).all())


def resolve_workspace(conn: sa.Connection, requested: str | None) -> tuple[uuid.UUID, str]:
    """Resolve ``(id, name)`` for the workspace to seed.

    An explicit ``--workspace`` wins; otherwise the workspace with the most
    locations is used, matching ``seed_demo_data.py`` so the two agree.
    """
    if requested:
        row = conn.execute(
            sa.text(
                "SELECT id, name FROM workspaces WHERE id::text = :q OR lower(name) = lower(:q) "
                "LIMIT 1"
            ),
            {"q": requested},
        ).first()
        if row is None:
            known = ", ".join(
                f"{r[1]} ({r[0]})" for r in _rows(conn, "SELECT id, name FROM workspaces")
            )
            raise SystemExit(f"No workspace matches {requested!r}. Known: {known}")
        return uuid.UUID(str(row[0])), str(row[1])

    rows = _rows(conn, "SELECT id, name, created_at FROM workspaces ORDER BY created_at")
    if not rows:
        raise SystemExit("No workspace exists — run seed_demo_roles.py first.")
    best, best_count = rows[0], -1
    for row in rows:
        count = int(
            conn.execute(
                sa.text("SELECT count(*) FROM locations WHERE workspace_id = :w"), {"w": row[0]}
            ).scalar()
            or 0
        )
        if count > best_count:
            best, best_count = row, count
    return uuid.UUID(str(best[0])), str(best[1])


def resolve_source_start(target_start: datetime.date, source_year: int) -> datetime.date:
    """Map ``target_start`` onto ``source_year``, preserving month **and** weekday.

    The model leans on day-of-week, month and holiday flags. A rebase that moves
    the calendar (e.g. December -> August) hands it features it never trained on
    and the forecast degrades for reasons that have nothing to do with the model.
    Anchoring on the same month/day and then advancing to the first matching
    weekday makes the offset a whole number of weeks, so both stay aligned.
    """
    try:
        anchor = datetime.date(source_year, target_start.month, target_start.day)
    except ValueError:
        # 29 Feb in a non-leap source year.
        anchor = datetime.date(source_year, target_start.month, 28)
    for delta in range(7):
        candidate = anchor + datetime.timedelta(days=delta)
        if candidate.weekday() == target_start.weekday():
            return candidate
    return anchor


def load_source_data() -> tuple[
    dict[str, dict[datetime.date, int]],
    dict[str, dict[datetime.date, tuple[int, int, float]]],
]:
    """Read the Recruit CSVs into ``{store: {date: ...}}`` maps.

    Returns visits (``visitors``) and reservations
    (``(reserved_count, reserved_visitors, mean_lead_days)``). Only the stores and
    columns actually used are kept; this never guesses a value.
    """
    if not VISIT_CSV.exists() or not RESERVE_CSV.exists():
        raise SystemExit(f"Recruit CSVs not found under {VISIT_CSV.parent}")

    wanted = set(STORE_FOR_LOCATION.values())

    visits: dict[str, dict[datetime.date, int]] = defaultdict(dict)
    with VISIT_CSV.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            store = row["air_store_id"]
            if store not in wanted:
                continue
            visits[store][datetime.date.fromisoformat(row["visit_date"])] = int(row["visitors"])

    raw_reserves: dict[str, dict[datetime.date, list[tuple[int, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    with RESERVE_CSV.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            store = row["air_store_id"]
            if store not in wanted:
                continue
            visit_day = datetime.date.fromisoformat(row["visit_datetime"][:10])
            booked_day = datetime.date.fromisoformat(row["reserve_datetime"][:10])
            lead = max(0, (visit_day - booked_day).days)
            raw_reserves[store][visit_day].append((int(row["reserve_visitors"]), lead))

    reserves: dict[str, dict[datetime.date, tuple[int, int, float]]] = defaultdict(dict)
    for store, by_day in raw_reserves.items():
        for day, entries in by_day.items():
            visitors = sum(v for v, _ in entries)
            leads = [lead for _, lead in entries]
            reserves[store][day] = (len(entries), visitors, round(statistics.mean(leads), 2))

    return visits, reserves


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------

_CLEAR_STATEMENTS: list[tuple[str, str]] = [
    # (label, SQL) — ordered so children go before parents.
    (
        "forecast_explanations",
        "DELETE FROM forecast_explanations WHERE forecast_id IN "
        "(SELECT id FROM forecasts WHERE workspace_id = :w)",
    ),
    ("forecasts", "DELETE FROM forecasts WHERE workspace_id = :w"),
    ("demand_observations", "DELETE FROM demand_observations WHERE workspace_id = :w"),
    (
        "reservations",
        "DELETE FROM reservations WHERE location_id IN "
        "(SELECT id FROM locations WHERE workspace_id = :w)",
    ),
    ("ingest_uploads", "DELETE FROM ingest_uploads WHERE workspace_id = :w"),
]


def clear_demand_surfaces(
    conn: sa.Connection,
    workspace_id: uuid.UUID,
    *,
    keep_alerts: bool,
    keep_quality: bool,
) -> None:
    """Delete the demand-derived rows for the workspace."""
    statements = list(_CLEAR_STATEMENTS)
    if not keep_quality:
        statements.append(("quality_issues", "DELETE FROM quality_issues WHERE workspace_id = :w"))
    if not keep_alerts:
        statements.append(("alerts", "DELETE FROM alerts WHERE workspace_id = :w"))

    print("Clearing demand-derived rows:")
    for label, sql in statements:
        result = conn.execute(sa.text(sql), {"w": str(workspace_id)})
        print(f"  {label:<22} -{result.rowcount or 0}")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def seed_ingest(
    conn: sa.Connection,
    workspace_id: uuid.UUID,
    locations: list[tuple[uuid.UUID, str, str]],
    visit_days: int,
    uploaded_by: uuid.UUID | None,
) -> dict[uuid.UUID, uuid.UUID]:
    """Create one committed ``ingest_uploads`` row per location, return ``loc->upload``."""
    uploads: dict[uuid.UUID, uuid.UUID] = {}
    for location_id, location_name, store in locations:
        upload_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO ingest_uploads (id, workspace_id, uploaded_by, kind, filename, "
                "status, row_count, error_count, preview, created_at) VALUES "
                "(:id, :w, :by, 'csv', :fn, 'committed', :rows, 0, CAST(:preview AS jsonb), :ts)"
            ),
            {
                "id": str(upload_id),
                "w": str(workspace_id),
                "by": str(uploaded_by) if uploaded_by else None,
                "fn": f"recruit_{store}_daily.csv",
                "rows": visit_days,
                "preview": '{"source": "recruit-restaurant-visitor", "columns": '
                '["air_store_id", "visit_date", "visitors"]}',
                "ts": datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=visit_days + 1),
            },
        )
        uploads[location_id] = upload_id
    return uploads


def seed_observations(
    conn: sa.Connection,
    workspace_id: uuid.UUID,
    locations: list[tuple[uuid.UUID, str, str]],
    visits: dict[str, dict[datetime.date, int]],
    uploads: dict[uuid.UUID, uuid.UUID],
    source_start: datetime.date,
    target_start: datetime.date,
    history_days: int,
) -> int:
    """Insert the rebased real daily demand; ``recorded_at`` is the visit day.

    ``recorded_at`` matters: the feature builder treats it as the moment the row
    became known, so a historical forecast published at ``as_of`` only sees the
    demand that had actually landed by then. Dating it to the visit evening is
    what makes the backfilled forecasts leakage-safe.
    """
    inserted = 0
    for location_id, _name, store in locations:
        series = visits.get(store, {})
        for offset in range(history_days):
            real_day = source_start + datetime.timedelta(days=offset)
            if real_day not in series:
                continue
            target_day = target_start + datetime.timedelta(days=offset)
            recorded = datetime.datetime.combine(
                target_day, datetime.time(23, 59), tzinfo=datetime.UTC
            )
            conn.execute(
                sa.text(
                    "INSERT INTO demand_observations (workspace_id, location_id, date, demand_qty, "
                    "upload_id, source_id, notes, recorded_at) VALUES "
                    "(:w, :loc, :d, :qty, :up, NULL, :note, :rec)"
                ),
                {
                    "w": str(workspace_id),
                    "loc": str(location_id),
                    "d": target_day,
                    "qty": int(series[real_day]),
                    "up": str(uploads[location_id]),
                    "note": f"Recruit daily visitors ({store})",
                    "rec": recorded,
                },
            )
            inserted += 1
    return inserted


def seed_reservations(
    conn: sa.Connection,
    locations: list[tuple[uuid.UUID, str, str]],
    reserves: dict[str, dict[datetime.date, tuple[int, int, float]]],
    source_start: datetime.date,
    target_start: datetime.date,
    history_days: int,
) -> int:
    """Insert the real reservation signal for the rebased window."""
    inserted = 0
    for location_id, _name, store in locations:
        series = reserves.get(store, {})
        for offset in range(history_days):
            real_day = source_start + datetime.timedelta(days=offset)
            if real_day not in series:
                continue
            count, visitors, lead = series[real_day]
            target_day = target_start + datetime.timedelta(days=offset)
            conn.execute(
                sa.text(
                    "INSERT INTO reservations (location_id, date, reserved_count, "
                    "reserved_visitors, lead_time_days) VALUES (:loc, :d, :c, :v, :lead)"
                ),
                {
                    "loc": str(location_id),
                    "d": target_day,
                    "c": count,
                    "v": visitors,
                    "lead": lead,
                },
            )
            inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------


def generate_forecasts(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    locations: list[tuple[uuid.UUID, str, str]],
    today: datetime.date,
    history_days: int,
    forecast_history_days: int,
    horizon: int,
) -> tuple[int, int]:
    """Publish real forecasts: recent past (for accuracy) and future (live).

    Historical rows use ``as_of = target - 1 day @ 03:00Z`` — the model runs the
    night before — so :mod:`app.services.accuracy` can pair them with the demand
    that later landed. Future rows use ``as_of = now``.
    """
    history_start = today - datetime.timedelta(days=history_days)
    hist_start = max(0, history_days - forecast_history_days)
    now = datetime.datetime.now(datetime.UTC)

    past = 0
    for location_id, _name, _store in locations:
        for offset in range(hist_start, history_days):
            target = history_start + datetime.timedelta(days=offset)
            as_of = datetime.datetime.combine(
                target - datetime.timedelta(days=1), datetime.time(3, 0), tzinfo=datetime.UTC
            )
            generate_forecast(
                db,
                workspace_id=workspace_id,
                location_id=location_id,
                target_date=target,
                as_of=as_of,
                horizon_days=1,
            )
            past += 1
    db.commit()

    future = 0
    for location_id, _name, _store in locations:
        # Start tomorrow: ``as_of`` must be strictly before the target date, so
        # today's own forecast is not a prediction and must not be published.
        for offset in range(1, horizon + 1):
            target = today + datetime.timedelta(days=offset)
            generate_forecast(
                db,
                workspace_id=workspace_id,
                location_id=location_id,
                target_date=target,
                as_of=now,
                horizon_days=horizon,
                explain=True,
            )
            future += 1
    db.commit()

    return past, future


# ---------------------------------------------------------------------------
# Derived quality issues and alerts
# ---------------------------------------------------------------------------


def derive_quality_issues(
    conn: sa.Connection,
    workspace_id: uuid.UUID,
    locations: list[tuple[uuid.UUID, str, str]],
    observations: list[tuple[uuid.UUID, datetime.date, int]],
    reservation_days: dict[uuid.UUID, int],
    rules: dict[str, uuid.UUID],
    window_days: int,
) -> int:
    """File real issues: genuine spikes, genuine gaps and a genuine missing signal.

    Every issue below is derived from the loaded data, so the Quality monitor
    reports the state the data is actually in rather than a scripted list. A
    window with no reservation rows at all is a real finding on this slice — the
    booking source is not feeding — and is surfaced as such.
    """
    by_location: dict[uuid.UUID, dict[datetime.date, int]] = defaultdict(dict)
    for location_id, day, qty in observations:
        by_location[location_id][day] = qty

    now = datetime.datetime.now(datetime.UTC)
    created = 0
    for location_id, location_name, _store in locations:
        series = by_location.get(location_id, {})
        if not series:
            continue
        days = sorted(series)
        median = statistics.median(series.values()) if series else 0.0

        # Missing reservation signal: no booking rows landed for the window.
        if reservation_days.get(location_id, 0) == 0:
            conn.execute(
                sa.text(
                    "INSERT INTO quality_issues (id, workspace_id, rule_id, location_id, severity, "
                    "summary, suggested_fix, created_at) VALUES "
                    "(gen_random_uuid(), :w, :rule, :loc, 'warning', :s, :fix, :ts)"
                ),
                {
                    "w": str(workspace_id),
                    # No rule owns "the booking source went quiet"; a null rule_id
                    # is the honest filing rather than borrowing gap_detection.
                    "rule": None,
                    "loc": str(location_id),
                    "s": (
                        f"No reservation signal for the last {window_days} days for "
                        f"{location_name}"
                    ),
                    "fix": (
                        "Connect the reservation source, or confirm the venue genuinely "
                        "takes no bookings."
                    ),
                    "ts": now - datetime.timedelta(hours=8),
                },
            )
            created += 1

        # Gaps: a hole between the first and last loaded day.
        span = (days[-1] - days[0]).days + 1
        missing = span - len(days)
        if missing > 0:
            summary = f"{missing} day(s) are missing for {location_name}"
            conn.execute(
                sa.text(
                    "INSERT INTO quality_issues (id, workspace_id, rule_id, location_id, severity, "
                    "summary, suggested_fix, created_at) VALUES "
                    "(gen_random_uuid(), :w, :rule, :loc, 'critical', :s, :fix, :ts)"
                ),
                {
                    "w": str(workspace_id),
                    "rule": str(rules[RULE_GAP]) if RULE_GAP in rules else None,
                    "loc": str(location_id),
                    "s": summary,
                    "fix": "Backfill from the POS export, or re-upload the affected week.",
                    "ts": now - datetime.timedelta(hours=6),
                },
            )
            created += 1

        # Spikes: days at or above SPIKE_RATIO x the window median.
        spikes = [d for d in days if series[d] >= SPIKE_RATIO * median] if median > 0 else []
        for day in spikes[:2]:
            summary = f"Implausible spike on {day.isoformat()}"
            conn.execute(
                sa.text(
                    "INSERT INTO quality_issues (id, workspace_id, rule_id, location_id, "
                    "severity, summary, suggested_fix, created_at) VALUES "
                    "(gen_random_uuid(), :w, :rule, :loc, 'warning', :s, :fix, :ts)"
                ),
                {
                    "w": str(workspace_id),
                    "rule": str(rules[RULE_SPIKE]) if RULE_SPIKE in rules else None,
                    "loc": str(location_id),
                    "s": summary,
                    "fix": "Confirm it is not a test entry; leave the day if the spike is real.",
                    "ts": now - datetime.timedelta(hours=12),
                },
            )
            created += 1
    return created


def derive_alerts(
    conn: sa.Connection,
    db: Session,
    workspace_id: uuid.UUID,
    alert_rules: dict[str, uuid.UUID],
    today: datetime.date,
    window_days: int,
) -> int:
    """Raise alerts from the accuracy the backfilled forecasts really achieve."""
    report = compute_accuracy(db, workspace_id, window_days=window_days, today=today)
    now = datetime.datetime.now(datetime.UTC)
    created = 0

    rule_for_signal = {
        "coverage": RULE_COVERAGE,
        "window_drift": RULE_DRIFT,
        "live_vs_test": RULE_FORECAST_ERROR,
    }
    severity_for = {"degraded": "critical", "watch": "warning"}

    for signal in report.get("signals") or []:
        severity = severity_for.get(signal.get("severity", ""))
        if severity is None:
            continue  # 'healthy'/informational signals are not alerts
        rule_name = rule_for_signal.get(signal.get("code", ""), RULE_FORECAST_ERROR)
        conn.execute(
            sa.text(
                "INSERT INTO alerts (id, workspace_id, rule_id, location_id, severity, title, "
                "body, created_at) VALUES "
                "(gen_random_uuid(), :w, :rule, NULL, :sev, :title, :body, :ts)"
            ),
            {
                "w": str(workspace_id),
                "rule": str(alert_rules[rule_name]) if rule_name in alert_rules else None,
                "sev": severity,
                "title": signal.get("label") or signal.get("code") or "Forecast signal",
                "body": signal.get("detail"),
                "ts": now - datetime.timedelta(hours=3),
            },
        )
        created += 1

    # Per-location error alerts, worst first, only when a location is off enough
    # to name — the same WAPE band the accuracy page uses.
    for entry in report.get("locations") or []:
        metrics = entry.get("metrics") or {}
        wape = metrics.get("wape_pct")
        if wape is None or wape < 25.0:
            continue
        severity = "critical" if wape >= 40.0 else "warning"
        conn.execute(
            sa.text(
                "INSERT INTO alerts (id, workspace_id, rule_id, location_id, severity, title, body, "
                "created_at) VALUES (gen_random_uuid(), :w, :rule, :loc, :sev, :title, :body, :ts)"
            ),
            {
                "w": str(workspace_id),
                "rule": (
                    str(alert_rules[RULE_FORECAST_ERROR])
                    if RULE_FORECAST_ERROR in alert_rules
                    else None
                ),
                "loc": entry.get("location_id"),
                "sev": severity,
                "title": f"Forecast error above threshold on {entry.get('location_name', 'location')}",
                "body": (
                    f"Live MAPE {wape:.1f}% of demand over the last {window_days} days "
                    f"against a {report.get('target_coverage', 90):.0f}% interval target."
                ),
                "ts": now - datetime.timedelta(hours=2),
            },
        )
        created += 1

    # One informational alert so the history has a non-warning entry.
    overall = report.get("overall")
    if overall:
        conn.execute(
            sa.text(
                "INSERT INTO alerts (id, workspace_id, rule_id, location_id, severity, title, body, "
                "created_at) VALUES (gen_random_uuid(), :w, NULL, NULL, 'info', :title, :body, :ts)"
            ),
            {
                "w": str(workspace_id),
                "title": "Real demand history imported",
                "body": (
                    f"{report.get('window_days')}-day window scored on real Recruit demand: "
                    f"MAE {overall.get('mae')}, WAPE {overall.get('wape_pct')}%, "
                    f"coverage {overall.get('coverage_pct')}%."
                ),
                "ts": now - datetime.timedelta(minutes=45),
            },
        )
        created += 1

    return created


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a real ~2-month demand sample.")
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace name or id to seed (default: the demo workspace)",
    )
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS)
    parser.add_argument("--forecast-history-days", type=int, default=DEFAULT_FORECAST_HISTORY_DAYS)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--source-year", type=int, default=SOURCE_YEAR)
    parser.add_argument("--keep-alerts", action="store_true", help="Do not clear existing alerts")
    parser.add_argument(
        "--keep-quality", action="store_true", help="Do not clear existing quality issues"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report the plan without writing")
    args = parser.parse_args()

    settings = get_settings()
    engine = sa.create_engine(settings.database_url)
    factory = get_session_factory()

    today = datetime.datetime.now(datetime.UTC).date()
    target_start = today - datetime.timedelta(days=args.history_days)
    source_start = resolve_source_start(target_start, args.source_year)

    print("Loading Recruit source data ...")
    visits, reserves = load_source_data()
    print(f"  stores: {', '.join(sorted(visits))}")

    with engine.begin() as conn:
        workspace_id, workspace_name = resolve_workspace(conn, args.workspace)
        print(f"Workspace: {workspace_name} ({workspace_id})")

        location_rows = _rows(
            conn,
            "SELECT id, name FROM locations WHERE workspace_id = :w AND archived_at IS NULL "
            "ORDER BY created_at",
            {"w": str(workspace_id)},
        )
        if not location_rows:
            raise SystemExit("No active locations in this workspace.")

        # Map by name where the store is known; otherwise fall back positionally
        # so a renamed demo location still gets real demand.
        fallback_stores = list(STORE_FOR_LOCATION.values())
        locations: list[tuple[uuid.UUID, str, str]] = []
        for index, (loc_id, loc_name) in enumerate(
            [(uuid.UUID(str(r[0])), str(r[1])) for r in location_rows]
        ):
            store = STORE_FOR_LOCATION.get(loc_name)
            if store is None:
                store = fallback_stores[index % len(fallback_stores)]
            locations.append((loc_id, loc_name, store))

        for loc_id, loc_name, store in locations:
            print(f"  {loc_name:<24} <- {store}")
        print(f"  source window starts {source_start} (weekday/month matched to target)")

        user_row = conn.execute(
            sa.text(
                "SELECT id FROM users WHERE workspace_id = :w AND role::text = 'owner' LIMIT 1"
            ),
            {"w": str(workspace_id)},
        ).first()
        uploaded_by = uuid.UUID(str(user_row[0])) if user_row else None

        rules = {
            str(r[1]): uuid.UUID(str(r[0]))
            for r in _rows(
                conn,
                "SELECT id, name FROM quality_rules WHERE workspace_id = :w",
                {"w": str(workspace_id)},
            )
        }
        alert_rules = {
            str(r[1]): uuid.UUID(str(r[0]))
            for r in _rows(
                conn,
                "SELECT id, name FROM alert_rules WHERE workspace_id = :w",
                {"w": str(workspace_id)},
            )
        }

        if args.dry_run:
            print("\n[dry-run] would clear, ingest and regenerate. Nothing written.")
            return

        clear_demand_surfaces(
            conn,
            workspace_id,
            keep_alerts=args.keep_alerts,
            keep_quality=args.keep_quality,
        )

        uploads = seed_ingest(conn, workspace_id, locations, args.history_days, uploaded_by)
        obs = seed_observations(
            conn,
            workspace_id,
            locations,
            visits,
            uploads,
            source_start,
            target_start,
            args.history_days,
        )
        print(f"\nDemand observations      +{obs}")
        res = seed_reservations(
            conn, locations, reserves, source_start, target_start, args.history_days
        )
        print(f"Reservations             +{res}")
        print(
            f"  source: {source_start} .. {source_start + datetime.timedelta(days=args.history_days - 1)}"
        )
        print(f"  target: {target_start} .. {today - datetime.timedelta(days=1)}")

    # Forecast generation needs its own session lifecycle (generate_forecast
    # flushes through the ORM), so it runs outside the clear transaction.
    db = factory()
    try:
        past, future = generate_forecasts(
            db,
            workspace_id=workspace_id,
            locations=locations,
            today=today,
            history_days=args.history_days,
            forecast_history_days=args.forecast_history_days,
            horizon=args.horizon,
        )
        print(f"Forecasts                +{past} historical, +{future} upcoming")

        with engine.begin() as conn:
            if not args.keep_quality:
                observation_rows = _rows(
                    conn,
                    "SELECT location_id, date, demand_qty FROM demand_observations "
                    "WHERE workspace_id = :w",
                    {"w": str(workspace_id)},
                )
                reservation_days: dict[uuid.UUID, int] = defaultdict(int)
                for row in _rows(
                    conn,
                    "SELECT r.location_id, count(*) FROM reservations r "
                    "JOIN locations l ON l.id = r.location_id WHERE l.workspace_id = :w "
                    "GROUP BY r.location_id",
                    {"w": str(workspace_id)},
                ):
                    reservation_days[uuid.UUID(str(row[0]))] = int(row[1])
                created = derive_quality_issues(
                    conn,
                    workspace_id,
                    locations,
                    observation_rows,
                    reservation_days,
                    rules,
                    args.history_days,
                )
                print(f"Quality issues (derived) +{created}")
            if not args.keep_alerts:
                created = derive_alerts(
                    conn,
                    db,
                    workspace_id,
                    alert_rules,
                    today,
                    window_days=min(28, args.forecast_history_days),
                )
                print(f"Alerts (derived)         +{created}")
    finally:
        db.close()

    # Final report.
    with engine.begin() as conn:
        print("\nRow counts now:")
        for label, sql in (
            (
                "demand_observations",
                "SELECT count(*) FROM demand_observations WHERE workspace_id = :w",
            ),
            ("forecasts", "SELECT count(*) FROM forecasts WHERE workspace_id = :w"),
            (
                "forecast_explanations",
                "SELECT count(*) FROM forecast_explanations e JOIN forecasts f ON f.id = e.forecast_id "
                "WHERE f.workspace_id = :w",
            ),
            (
                "reservations",
                "SELECT count(*) FROM reservations r JOIN locations l ON l.id = r.location_id "
                "WHERE l.workspace_id = :w",
            ),
            ("quality_issues", "SELECT count(*) FROM quality_issues WHERE workspace_id = :w"),
            ("alerts", "SELECT count(*) FROM alerts WHERE workspace_id = :w"),
        ):
            count = conn.execute(sa.text(sql), {"w": str(workspace_id)}).scalar()
            print(f"  {label:<24} {count}")
    print("DONE")


if __name__ == "__main__":
    main()
