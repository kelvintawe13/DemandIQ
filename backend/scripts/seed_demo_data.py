#!/usr/bin/env python3
"""Seed rich demo data so every operator surface has something to show.

``seed_demo_roles.py`` creates the signable seats; this fills the screens those
seats land on. The motivation is the same: a workspace with a handful of rows
demonstrates nothing. Screens like Reports, Alerts, Quality, Devices, Webhooks,
Pipeline, Features and Approvals render empty states when their tables are
empty, which reads as "not built" rather than "no data yet".

What it seeds, all scoped to the demo workspace resolved the same way
``seed_demo_roles.py`` resolves it (the workspace owning the demo rows):

  alerts                 mixed severities, some unread / resolved
  quality_issues         open issues across severities + resolved history
  reports                completed / queued rows across the real templates
  report_schedules       weekly + monthly deliveries with recipients
  feature_definitions    the production feature set from the model contract
  pipeline_runs          ingest / training / calibration / forecasting history
  webhook_endpoints      two endpoints with real subscriptions
  webhook_deliveries     a delivery log incl. one non-2xx retry
  preparation_policies   all four policy kinds, one active
  policy_simulations     SR / PS / UD results per location
  change_requests        pending approvals that respect the two-person rule
  devices                an enrolled fleet with consent state
  device_grants          capability grants, one expiring
  device_events          heartbeats / syncs / errors
  mirror_sessions        one pending (two-person) and one ended
  menu_groups            perishability mix for the menu view
  reservations           booking signal per location/date
  waste_observations     a slice of the URBAN-WASTE evidence table
  user_preferences       theme / density / default location
  user_notification_settings  digest + alert toggles

Idempotent: every insert is guarded by a natural key (``ON CONFLICT DO NOTHING``
or an existence check), so re-running refreshes nothing and duplicates nothing.
It never deletes.

Usage:
    backend/.venv/bin/python backend/scripts/seed_demo_data.py
    backend/.venv/bin/python backend/scripts/seed_demo_data.py --workspace "DemandIQ Demo"
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import random
import secrets
import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.security.hashing import hash_password

# The workspace this seeds into, resolved below. The seat accounts live here, so
# the data has to live here too.
DEFAULT_WORKSPACE_HINT = "DemandIQ Demo"

# Deterministic so two runs produce the same demo and screenshots stay stable.
# Not a security context: this only jitters demo timestamps and volumes.
RNG = random.Random(20261002)  # noqa: S311

# The production feature contract — mirrors ``model/artifacts/runs/latest``
# (feature_set_version fs-2026.09.2, 26 predictors). Names come from the
# feature spec; the UI shows group, dtype and coverage.
FEATURE_SET_VERSION = "fs-2026.09.2"
FEATURE_DEFINITIONS: list[tuple[str, str, str, str, float, bool, bool]] = [
    # (name, group, dtype, description, coverage_pct, leakage_safe, in_production)
    ("lag_1", "History", "float", "Visitors on the previous day.", 99.9, True, True),
    ("lag_7", "History", "float", "Visitors the same weekday last week.", 99.6, True, True),
    ("lag_14", "History", "float", "Visitors the same weekday two weeks ago.", 99.3, True, True),
    ("roll_mean_7", "History", "float", "Trailing 7-day mean, shifted one day.", 99.4, True, True),
    (
        "roll_mean_28",
        "History",
        "float",
        "Trailing 28-day mean, shifted one day.",
        98.1,
        True,
        True,
    ),
    (
        "roll_std_28",
        "History",
        "float",
        "Trailing 28-day volatility, shifted one day.",
        98.1,
        True,
        True,
    ),
    ("roll_max_7", "History", "float", "Trailing 7-day peak, shifted one day.", 99.4, True, True),
    ("roll_max_28", "History", "float", "Trailing 28-day peak, shifted one day.", 98.1, True, True),
    (
        "roll_p90_28",
        "History",
        "float",
        "Trailing 28-day 90th percentile, shifted one day.",
        98.1,
        True,
        True,
    ),
    (
        "dow_mean_8w",
        "Seasonality",
        "float",
        "Weekday mean over the last 8 weeks.",
        97.8,
        True,
        True,
    ),
    (
        "dow_std_8w",
        "Seasonality",
        "float",
        "Weekday volatility over the last 8 weeks.",
        97.8,
        True,
        True,
    ),
    ("trend_7_28", "Seasonality", "float", "Short vs long rolling-mean ratio.", 98.0, True, True),
    ("dow", "Calendar", "int", "Day of week, 0 = Monday.", 100.0, True, True),
    ("is_weekend", "Calendar", "int", "Weekend flag.", 100.0, True, True),
    ("month", "Calendar", "int", "Calendar month.", 100.0, True, True),
    ("week_of_year", "Calendar", "int", "ISO week of year.", 100.0, True, True),
    ("is_month_end", "Calendar", "int", "Month-end flag.", 100.0, True, True),
    ("days_to_month_end", "Calendar", "int", "Days until month end.", 100.0, True, True),
    ("holiday_flg", "Holiday", "int", "Public holiday flag.", 100.0, True, True),
    ("is_holiday", "Holiday", "int", "Holiday interaction flag.", 100.0, True, True),
    ("day_before_holiday", "Holiday", "int", "Eve of a public holiday.", 100.0, True, True),
    ("day_after_holiday", "Holiday", "int", "Day after a public holiday.", 100.0, True, True),
    (
        "is_weekend_or_holiday",
        "Holiday",
        "int",
        "Weekend or holiday combined flag.",
        100.0,
        True,
        True,
    ),
    (
        "rsv_visitors",
        "Reservations",
        "float",
        "Reserved visitors booked before the visit day.",
        61.4,
        True,
        True,
    ),
    (
        "rsv_count",
        "Reservations",
        "float",
        "Reservation count booked before the visit day.",
        61.4,
        True,
        True,
    ),
    (
        "rsv_visitors_ratio",
        "Reservations",
        "float",
        "Reserved visitors over trailing demand.",
        60.9,
        True,
        True,
    ),
    (
        "lag_28",
        "History",
        "float",
        "Visitors four weeks ago — candidate, not promoted.",
        96.2,
        True,
        False,
    ),
    (
        "waste_kg",
        "Waste",
        "float",
        "URBAN-WASTE measurement — evidence only, never joined.",
        4.8,
        False,
        False,
    ),
]

PIPELINE_JOBS: list[tuple[str, str, int, int, str]] = [
    # (job_type, status, rows_processed, duration_ms, detail)
    ("ingestion", "success", 57866, 8421, "Recruit holdout slice loaded (829 stores)."),
    ("training", "success", 193413, 412300, "LightGBM booster trained, best_iteration=200."),
    ("calibration", "success", 57866, 5230, "Mondrian ACI at 90% target coverage."),
    ("forecasting", "success", 829, 1288, "Daily batch written for every active store."),
    ("ingestion", "failed", 0, 640, "Connector Santa OCR unreachable — retried once."),
    ("training", "success", 193413, 398110, "Weekly retrain, same seed, same metrics."),
    ("forecasting", "running", None, None, "Batch in progress across 3 locations."),
]

ALERT_SEED: list[tuple[str, str, str, bool, bool]] = [
    # (severity, title, body, read, resolved)
    (
        "critical",
        "Forecast error above threshold",
        "Kigali Heights Bistro MAE crossed 20 for the last 7 days (rule: forecast_error).",
        False,
        False,
    ),
    (
        "critical",
        "Conformal coverage breach",
        "Empirical coverage fell to 84.2% against a 90% target on Remera Grill.",
        False,
        False,
    ),
    (
        "warning",
        "Feature drift detected",
        "rsv_visitors shifted beyond the PSI threshold this week.",
        False,
        False,
    ),
    (
        "warning",
        "Connector stale",
        "The Santa OCR connector has not synced in 31 hours (threshold: 24).",
        True,
        False,
    ),
    (
        "info",
        "Weekly retrain completed",
        "LightGBM retrained with best_iteration=200; metrics unchanged.",
        True,
        True,
    ),
    (
        "info",
        "Report delivered",
        "Demand summary PDF was delivered to 2 recipients.",
        True,
        True,
    ),
]

QUALITY_ISSUE_SEED: list[tuple[str, str, str, bool, bool]] = [
    (
        "critical",
        "Three consecutive days are missing for Remera Grill",
        "Backfill from the POS export, or re-upload the week's file.",
        False,
        False,
    ),
    (
        "critical",
        "Duplicate day across channels for Kigali Heights Bistro",
        "Two rows share the same (location, date); keep the POS row and drop the OCR row.",
        False,
        False,
    ),
    (
        "warning",
        "Implausible spike on 2026-09-18",
        "Demand is 4.1x the trailing median; confirm it is not a test entry.",
        False,
        False,
    ),
    (
        "warning",
        "OCR confidence below 80% on 6 handwritten rows",
        "Re-photograph the log sheet with better lighting.",
        False,
        False,
    ),
    (
        "info",
        "Schema drift in the latest CSV upload",
        "An unrecognised column was ignored: 'notes_extra'.",
        True,
        False,
    ),
    (
        "warning",
        "Stale connector: Santa OCR",
        "Last successful sync was 31 hours ago.",
        True,
        True,
    ),
]

CHANGE_REQUEST_SEED: list[tuple[str, str, str, str, str]] = [
    # (kind, title, before, after, impact)
    (
        "model_promotion",
        "Promote CatBoost to production",
        "lightgbm-fs-2026.09.2-it200",
        "catboost-fs-2026.09.2-it868",
        "MAE improves 6.702 -> 6.690; needs a second approver.",
    ),
    (
        "policy_change",
        "Raise the preparation quantile to 0.85",
        "uncertainty_aware @ 0.80",
        "uncertainty_aware @ 0.85",
        "More surplus on busy days, ~4% more prep volume.",
    ),
    (
        "access_grant",
        "Grant Analyst the modelling console",
        "analyst: read-only",
        "analyst: experiments.manage",
        "Analyst can run experiments without admin help.",
    ),
]

MENU_GROUPS: list[tuple[str, int, float, str]] = [
    ("Grilled meats", 14, 31.5, "high"),
    ("Rice & starches", 9, 24.0, "low"),
    ("Vegetable sides", 11, 18.5, "high"),
    ("Sauces & condiments", 8, 12.0, "medium"),
    ("Desserts", 7, 9.5, "high"),
    ("Beverages", 12, 4.5, "low"),
]

DEVICE_SEED: list[tuple[str, str, str, str, bool]] = [
    # (label, device_type, platform, app_version, consent)
    ("Till 1 — Kigali Heights", "pos", "android", "2.4.1", True),
    ("Till 2 — Kigali Heights", "pos", "android", "2.4.1", True),
    ("Back-office tablet", "tablet", "android", "2.3.9", False),
    ("Kitchen display — Remera", "kds", "android", "2.4.0", True),
    ("Manager phone", "phone", "ios", "2.4.1", False),
    ("Legacy terminal", "pos", "linux", "1.9.2", False),
]

DEVICE_EVENT_TYPES = ["heartbeat", "sync", "error", "app_update", "enroll"]


def _uuid_rows(conn: sa.Connection, sql: str, params: dict | None = None) -> list:
    return list(conn.execute(sa.text(sql), params or {}).all())


def resolve_workspace(conn: sa.Connection, requested: str | None) -> tuple[str, str]:
    """Return ``(id, name)`` for the workspace that owns the demo rows.

    Same rule as ``seed_demo_roles.py``: the workspace with the most locations
    wins, because that is where the seats were placed and the data has to agree.
    """

    if requested:
        row = conn.execute(
            sa.text(
                "SELECT id, name FROM workspaces "
                "WHERE id::text = :q OR lower(name) = lower(:q) LIMIT 1"
            ),
            {"q": requested},
        ).first()
        if row is None:
            names = ", ".join(
                f"{r[1]} ({r[0]})" for r in _uuid_rows(conn, "SELECT id, name FROM workspaces")
            )
            raise SystemExit(f"No workspace matches {requested!r}. Known: {names}")
        return str(row[0]), str(row[1])

    rows = _uuid_rows(conn, "SELECT id, name, created_at FROM workspaces ORDER BY created_at")
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
    return str(best[0]), str(best[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed rich demo data for empty screens.")
    parser.add_argument(
        "--workspace",
        default=os.environ.get("DEMO_WORKSPACE") or DEFAULT_WORKSPACE_HINT,
        help="Workspace name or id to seed (default: the demo workspace)",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = sa.create_engine(settings.database_url)
    now = datetime.datetime.now(datetime.UTC)

    with engine.begin() as conn:
        ws_id, ws_name = resolve_workspace(conn, args.workspace)
        print(f"Workspace: {ws_name} ({ws_id})")

        users = _uuid_rows(
            conn,
            "SELECT id, email, role::text FROM users WHERE workspace_id = :w ORDER BY email",
            {"w": ws_id},
        )
        if not users:
            raise SystemExit("No users in this workspace — run seed_demo_roles.py first.")
        owner = next((u for u in users if u[2] == "owner"), users[0])
        admin = next((u for u in users if u[2] == "administrator"), users[0])
        locations = _uuid_rows(
            conn,
            "SELECT id, name FROM locations WHERE workspace_id = :w ORDER BY created_at",
            {"w": ws_id},
        )
        if not locations:
            raise SystemExit("No locations in this workspace; nothing to attach data to.")
        rules = _uuid_rows(
            conn,
            "SELECT id, name FROM quality_rules WHERE workspace_id = :w",
            {"w": ws_id},
        )
        rule_by_name = {r[1]: r[0] for r in rules}
        alert_rules = _uuid_rows(
            conn,
            "SELECT id, name FROM alert_rules WHERE workspace_id = :w",
            {"w": ws_id},
        )
        alert_rule_by_name = {r[1]: r[0] for r in alert_rules}
        policies = _uuid_rows(
            conn,
            "SELECT id, kind::text FROM preparation_policies WHERE workspace_id = :w",
            {"w": ws_id},
        )
        policy_by_kind = {p[1]: p[0] for p in policies}

        # ---------------------------------------------------------------- alerts
        created = 0
        for severity, title, body, read, resolved in ALERT_SEED:
            exists = conn.execute(
                sa.text("SELECT 1 FROM alerts WHERE workspace_id = :w AND title = :t LIMIT 1"),
                {"w": ws_id, "t": title},
            ).first()
            if exists:
                continue
            rule_id = None
            for name in ("forecast_error", "coverage_breach", "drift", "connector_health"):
                if name.split("_")[0] in title.lower() or name in body.lower():
                    rule_id = alert_rule_by_name.get(name)
                    break
            conn.execute(
                sa.text(
                    "INSERT INTO alerts (id, workspace_id, rule_id, location_id, severity, title, "
                    "body, read_at, resolved_at, created_at) VALUES "
                    "(gen_random_uuid(), :w, :rule, :loc, :sev, :title, :body, :read, :resolved, :ts)"
                ),
                {
                    "w": ws_id,
                    "rule": rule_id,
                    "loc": str(RNG.choice(locations)[0]) if severity != "info" else None,
                    "sev": severity,
                    "title": title,
                    "body": body,
                    "read": now - datetime.timedelta(hours=3) if read else None,
                    "resolved": now - datetime.timedelta(hours=2) if resolved else None,
                    "ts": now - datetime.timedelta(hours=RNG.randint(1, 72)),
                },
            )
            created += 1
        print(f"  alerts                 +{created}")

        # -------------------------------------------------------- quality issues
        created = 0
        for severity, summary, fix, dismissed, resolved in QUALITY_ISSUE_SEED:
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM quality_issues WHERE workspace_id = :w AND summary = :s LIMIT 1"
                ),
                {"w": ws_id, "s": summary},
            ).first()
            if exists:
                continue
            rule_id = rule_by_name.get("gap_detection") if "missing" in summary.lower() else None
            if "duplicate" in summary.lower():
                rule_id = rule_by_name.get("duplicate_conflict")
            elif "spike" in summary.lower():
                rule_id = rule_by_name.get("spike_detection")
            elif "stale" in summary.lower():
                rule_id = rule_by_name.get("stale_connector")
            elif "drift" in summary.lower():
                rule_id = rule_by_name.get("schema_drift")
            conn.execute(
                sa.text(
                    "INSERT INTO quality_issues (id, workspace_id, rule_id, location_id, severity, "
                    "summary, suggested_fix, resolved_at, dismissed_at, created_at) VALUES "
                    "(gen_random_uuid(), :w, :rule, :loc, :sev, :summary, :fix, :resolved, :dismissed, :ts)"
                ),
                {
                    "w": ws_id,
                    "rule": rule_id,
                    "loc": str(RNG.choice(locations)[0]),
                    "sev": severity,
                    "summary": summary,
                    "fix": fix,
                    "resolved": now - datetime.timedelta(days=1) if resolved else None,
                    "dismissed": now - datetime.timedelta(days=2) if dismissed else None,
                    "ts": now - datetime.timedelta(hours=RNG.randint(2, 96)),
                },
            )
            created += 1
        print(f"  quality_issues         +{created}")

        # ------------------------------------------------------------- reports
        templates = [
            ("demand_summary", "pdf"),
            ("forecast_accuracy", "pdf"),
            ("waste_reduction", "xlsx"),
            ("data_quality_audit", "csv"),
        ]
        created = 0
        for index, (template, fmt) in enumerate(templates):
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM reports WHERE workspace_id = :w AND template = :t AND format = :f LIMIT 1"
                ),
                {"w": ws_id, "t": template, "f": fmt},
            ).first()
            if exists:
                continue
            period_end = (now - datetime.timedelta(days=index * 7)).date()
            period_start = period_end - datetime.timedelta(days=30)
            conn.execute(
                sa.text(
                    "INSERT INTO reports (id, workspace_id, template, period_start, period_end, "
                    "format, status, file_uri, size_bytes, generated_by, created_at) VALUES "
                    "(gen_random_uuid(), :w, :t, :ps, :pe, :f, :status, :uri, :size, :by, :ts)"
                ),
                {
                    "w": ws_id,
                    "t": template,
                    "ps": period_start,
                    "pe": period_end,
                    "f": fmt,
                    "status": "completed",
                    "uri": f"s3://demandiq-demo/reports/{template}-{period_end.isoformat()}.{fmt}",
                    "size": RNG.randint(48_000, 820_000),
                    "by": str(owner[0]),
                    "ts": now - datetime.timedelta(days=index * 7, hours=2),
                },
            )
            created += 1
        # One still-queued report so the status column has variety.
        if not conn.execute(
            sa.text("SELECT 1 FROM reports WHERE workspace_id = :w AND status = 'queued' LIMIT 1"),
            {"w": ws_id},
        ).first():
            conn.execute(
                sa.text(
                    "INSERT INTO reports (id, workspace_id, template, period_start, period_end, "
                    "format, status, generated_by, created_at) VALUES "
                    "(gen_random_uuid(), :w, 'demand_summary', :ps, :pe, 'pdf', 'queued', :by, :ts)"
                ),
                {
                    "w": ws_id,
                    "ps": (now - datetime.timedelta(days=2)).date(),
                    "pe": now.date(),
                    "by": str(admin[0]),
                    "ts": now - datetime.timedelta(minutes=12),
                },
            )
            created += 1
        print(f"  reports                +{created}")

        # ---------------------------------------------------- report schedules
        schedules = [
            ("demand_summary", "weekly", ["ops@demandiq.app", "owner@demandiq.app"], "pdf"),
            ("forecast_accuracy", "monthly", ["analytics@demandiq.app"], "pdf"),
            ("waste_reduction", "monthly", ["kitchen@demandiq.app"], "xlsx"),
        ]
        created = 0
        for template, cadence, recipients, fmt in schedules:
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM report_schedules WHERE workspace_id = :w AND template = :t LIMIT 1"
                ),
                {"w": ws_id, "t": template},
            ).first()
            if exists:
                continue
            next_run = now + datetime.timedelta(days=7 if cadence == "weekly" else 30)
            conn.execute(
                sa.text(
                    "INSERT INTO report_schedules (id, workspace_id, template, cadence, recipients, "
                    "format, enabled, next_run_at) VALUES "
                    "(gen_random_uuid(), :w, :t, :c, :r, :f, true, :n)"
                ),
                {
                    "w": ws_id,
                    "t": template,
                    "c": cadence,
                    "r": recipients,
                    "f": fmt,
                    "n": next_run,
                },
            )
            created += 1
        print(f"  report_schedules       +{created}")

        # --------------------------------------------------- feature_definitions
        created = 0
        for name, group, dtype, description, coverage, safe, prod in FEATURE_DEFINITIONS:
            result = conn.execute(
                sa.text(
                    "INSERT INTO feature_definitions (id, name, feature_group, dtype, description, "
                    "coverage_pct, leakage_safe, in_production) VALUES "
                    "(gen_random_uuid(), :n, :g, :d, :desc, :cov, :safe, :prod) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                {
                    "n": name,
                    "g": group,
                    "d": dtype,
                    "desc": description,
                    "cov": coverage,
                    "safe": safe,
                    "prod": prod,
                },
            )
            created += result.rowcount or 0
        print(f"  feature_definitions    +{created}")

        # ---------------------------------------------------------- pipeline runs
        created = 0
        for index, (job, status, rows, duration, detail) in enumerate(PIPELINE_JOBS):
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM pipeline_runs WHERE workspace_id = :w AND detail = :d LIMIT 1"
                ),
                {"w": ws_id, "d": detail},
            ).first()
            if exists:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO pipeline_runs (id, workspace_id, job_type, status, rows_processed, "
                    "duration_ms, detail, started_at) VALUES "
                    "(gen_random_uuid(), :w, :j, :s, :rows, :dur, :d, :ts)"
                ),
                {
                    "w": ws_id,
                    "j": job,
                    "s": status,
                    "rows": rows,
                    "dur": duration,
                    "d": detail,
                    "ts": now - datetime.timedelta(hours=index * 9 + 1),
                },
            )
            created += 1
        print(f"  pipeline_runs          +{created}")

        # -------------------------------------------------------------- webhooks
        endpoints = [
            (
                "https://hooks.demantiq.app/demand-events",
                ["forecast.generated", "forecast.updated", "alert.raised"],
            ),
            (
                "https://ops.example.com/demandiq/alerts",
                ["alert.raised", "alert.resolved"],
            ),
        ]
        created = 0
        endpoint_ids: list[str] = []
        for url, events in endpoints:
            row = conn.execute(
                sa.text("SELECT id FROM webhook_endpoints WHERE workspace_id = :w AND url = :u"),
                {"w": ws_id, "u": url},
            ).first()
            if row is None:
                row = conn.execute(
                    sa.text(
                        "INSERT INTO webhook_endpoints (id, workspace_id, url, events, secret, status, "
                        "created_at) VALUES (gen_random_uuid(), :w, :u, :e, :s, 'active', :ts) "
                        "RETURNING id"
                    ),
                    {
                        "w": ws_id,
                        "u": url,
                        "e": events,
                        "s": secrets.token_hex(24),
                        "ts": now - datetime.timedelta(days=14),
                    },
                ).first()
                created += 1
            endpoint_ids.append(str(row[0]))

        deliveries = [
            ("forecast.generated", 200, 1),
            ("alert.raised", 200, 1),
            ("forecast.updated", 200, 1),
            ("alert.raised", 500, 1),
            ("alert.raised", 200, 2),
            ("alert.resolved", 200, 1),
        ]
        delivered = 0
        for endpoint_id in endpoint_ids:
            for event, code, attempt in deliveries:
                exists = conn.execute(
                    sa.text(
                        "SELECT 1 FROM webhook_deliveries WHERE endpoint_id = :e AND event = :ev "
                        "AND attempt = :a LIMIT 1"
                    ),
                    {"e": endpoint_id, "ev": event, "a": attempt},
                ).first()
                if exists:
                    continue
                conn.execute(
                    sa.text(
                        "INSERT INTO webhook_deliveries (endpoint_id, event, payload, response_code, "
                        "attempt, delivered_at) VALUES (:e, :ev, CAST(:p AS jsonb), :code, :a, :ts)"
                    ),
                    {
                        "e": endpoint_id,
                        "ev": event,
                        "p": json.dumps({"event": event, "workspace_id": ws_id}),
                        "code": code,
                        "a": attempt,
                        "ts": now - datetime.timedelta(hours=RNG.randint(1, 48)),
                    },
                )
                delivered += 1
        print(f"  webhook_endpoints      +{created}")
        print(f"  webhook_deliveries     +{delivered}")

        # ------------------------------------------------------- policy + sims
        if not conn.execute(
            sa.text(
                "SELECT 1 FROM preparation_policies WHERE workspace_id = :w AND kind = 'risk_aware'"
            ),
            {"w": ws_id},
        ).first():
            conn.execute(
                sa.text(
                    "INSERT INTO preparation_policies (id, workspace_id, kind, risk_weight, is_active) "
                    "VALUES (gen_random_uuid(), :w, 'risk_aware', 0.65, false)"
                ),
                {"w": ws_id},
            )
            print("  preparation_policies   +1 (risk_aware)")

        policies = _uuid_rows(
            conn,
            "SELECT id, kind::text FROM preparation_policies WHERE workspace_id = :w",
            {"w": ws_id},
        )
        policy_by_kind = {p[1]: p[0] for p in policies}
        created = 0
        for location_id, location_name in locations:
            for policy_kind in ("point_forecast", "uncertainty_aware", "historical_average"):
                policy_id = policy_by_kind.get(policy_kind)
                if policy_id is None:
                    continue
                period_end = now.date()
                period_start = period_end - datetime.timedelta(days=28)
                exists = conn.execute(
                    sa.text(
                        "SELECT 1 FROM policy_simulations WHERE workspace_id = :w AND policy_id = :p "
                        "AND location_id = :l AND period_start = :ps LIMIT 1"
                    ),
                    {"w": ws_id, "p": policy_id, "l": location_id, "ps": period_start},
                ).first()
                if exists:
                    continue
                prepared = round(RNG.uniform(900, 2600), 2)
                surplus_sr = round(prepared * RNG.uniform(0.05, 0.12), 2)
                surplus_ps = round(prepared * RNG.uniform(0.03, 0.09), 2)
                unmet = round(prepared * RNG.uniform(0.01, 0.06), 2)
                conn.execute(
                    sa.text(
                        "INSERT INTO policy_simulations (id, workspace_id, policy_id, location_id, "
                        "period_start, period_end, prepared_total, surplus_sr, surplus_ps, unmet_ud, "
                        "surplus_days, unmet_days, simulated_at) VALUES "
                        "(gen_random_uuid(), :w, :p, :l, :ps, :pe, :prep, :sr, :psu, :ud, :sd, :ud_d, :ts)"
                    ),
                    {
                        "w": ws_id,
                        "p": policy_id,
                        "l": location_id,
                        "ps": period_start,
                        "pe": period_end,
                        "prep": prepared,
                        "sr": surplus_sr,
                        "psu": surplus_ps,
                        "ud": unmet,
                        "sd": RNG.randint(2, 9),
                        "ud_d": RNG.randint(0, 4),
                        "ts": now - datetime.timedelta(days=1),
                    },
                )
                created += 1
        print(f"  policy_simulations     +{created}")

        # ------------------------------------------------------- change requests
        created = 0
        for kind, title, before, after, impact in CHANGE_REQUEST_SEED:
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM change_requests WHERE workspace_id = :w AND title = :t LIMIT 1"
                ),
                {"w": ws_id, "t": title},
            ).first()
            if exists:
                continue
            # Requested by the analyst, decided by nobody yet — the two-person
            # rule needs a *different* approver, so leaving decided_by NULL is
            # the honest pending state.
            requester = next((u for u in users if u[2] == "analyst"), owner)
            conn.execute(
                sa.text(
                    "INSERT INTO change_requests (id, workspace_id, kind, title, before_value, "
                    "after_value, impact, status, requested_by, created_at) VALUES "
                    "(gen_random_uuid(), :w, :k, :t, :b, :a, :i, 'pending', :by, :ts)"
                ),
                {
                    "w": ws_id,
                    "k": kind,
                    "t": title,
                    "b": before,
                    "a": after,
                    "i": impact,
                    "by": str(requester[0]),
                    "ts": now - datetime.timedelta(hours=RNG.randint(4, 40)),
                },
            )
            created += 1
        print(f"  change_requests        +{created}")

        # ---------------------------------------------------------------- devices
        created = 0
        device_ids: list[str] = []
        for index, (label, dtype, platform, version, consent) in enumerate(DEVICE_SEED):
            row = conn.execute(
                sa.text("SELECT id FROM devices WHERE workspace_id = :w AND label = :l"),
                {"w": ws_id, "l": label},
            ).first()
            if row is None:
                prefix = "did_" + secrets.token_hex(6)
                secret = secrets.token_urlsafe(32)
                status = "revoked" if "Legacy" in label else "active"
                row = conn.execute(
                    sa.text(
                        "INSERT INTO devices (id, workspace_id, location_id, label, device_type, "
                        "platform, app_version, credential_prefix, credential_hash, scopes, status, "
                        "last_seen_at, enrolled_at, enrolled_by, revoked_at, mirroring_consent_at) VALUES "
                        "(gen_random_uuid(), :w, :loc, :label, :dt, :plat, :ver, :prefix, :hash, :scopes, "
                        ":status, :seen, :enrolled, :by, :revoked, :consent) RETURNING id"
                    ),
                    {
                        "w": ws_id,
                        "loc": str(locations[index % len(locations)][0]),
                        "label": label,
                        "dt": dtype,
                        "plat": platform,
                        "ver": version,
                        "prefix": prefix,
                        "hash": hash_password(secret),
                        "scopes": ["device.sync"],
                        "status": status,
                        "seen": (
                            now - datetime.timedelta(minutes=RNG.randint(2, 600))
                            if status == "active"
                            else None
                        ),
                        "enrolled": now - datetime.timedelta(days=RNG.randint(10, 90)),
                        "by": str(admin[0]),
                        "revoked": (
                            now - datetime.timedelta(days=5) if status == "revoked" else None
                        ),
                        "consent": now - datetime.timedelta(days=30) if consent else None,
                    },
                ).first()
                created += 1
            device_ids.append(str(row[0]))
        print(f"  devices                +{created}")

        # device grants — one live, one expiring, one revoked
        created = 0
        for index, device_id in enumerate(device_ids[:3]):
            exists = conn.execute(
                sa.text("SELECT 1 FROM device_grants WHERE device_id = :d LIMIT 1"),
                {"d": device_id},
            ).first()
            if exists:
                continue
            expires = None
            revoked_at = None
            reason = "Till needs to sync daily demand."
            if index == 1:
                expires = now + datetime.timedelta(days=3)
                reason = "Temporary access for the stocktake week."
            elif index == 2:
                revoked_at = now - datetime.timedelta(days=2)
                reason = "Access withdrawn after the device was replaced."
            conn.execute(
                sa.text(
                    "INSERT INTO device_grants (id, workspace_id, device_id, capability, granted_by, "
                    "granted_at, expires_at, revoked_at, reason) VALUES "
                    "(gen_random_uuid(), :w, :d, 'device.sync', :by, :ts, :exp, :rev, :r)"
                ),
                {
                    "w": ws_id,
                    "d": device_id,
                    "by": str(admin[0]),
                    "ts": now - datetime.timedelta(days=20),
                    "exp": expires,
                    "rev": revoked_at,
                    "r": reason,
                },
            )
            created += 1
        print(f"  device_grants          +{created}")

        # device events — a recent activity window per active device
        created = 0
        for device_id in device_ids:
            exists = conn.execute(
                sa.text("SELECT 1 FROM device_events WHERE device_id = :d LIMIT 1"),
                {"d": device_id},
            ).first()
            if exists:
                continue
            for step in range(RNG.randint(4, 8)):
                event_type = RNG.choice(DEVICE_EVENT_TYPES)
                payload = {"app_version": "2.4.1", "ok": event_type != "error"}
                if event_type == "error":
                    payload["message"] = "Sync failed: connection reset by peer."
                conn.execute(
                    sa.text(
                        "INSERT INTO device_events (workspace_id, device_id, event_type, payload, "
                        "recorded_at) VALUES (:w, :d, :t, CAST(:p AS jsonb), :ts)"
                    ),
                    {
                        "w": ws_id,
                        "d": device_id,
                        "t": event_type,
                        "p": json.dumps(payload),
                        "ts": now - datetime.timedelta(hours=step * 6 + RNG.randint(0, 3)),
                    },
                )
                created += 1
        print(f"  device_events          +{created}")

        # mirror sessions — one pending (two-person), one ended
        created = 0
        active_device = device_ids[0]
        requester = next((u for u in users if u[2] == "operator"), owner)
        if not conn.execute(
            sa.text("SELECT 1 FROM mirror_sessions WHERE workspace_id = :w LIMIT 1"), {"w": ws_id}
        ).first():
            conn.execute(
                sa.text(
                    "INSERT INTO mirror_sessions (id, workspace_id, device_id, requested_by, status, "
                    "requested_at, reason) VALUES (gen_random_uuid(), :w, :d, :by, 'pending', :ts, :r)"
                ),
                {
                    "w": ws_id,
                    "d": active_device,
                    "by": str(requester[0]),
                    "ts": now - datetime.timedelta(minutes=25),
                    "r": "Support call: till shows a sync error the operator cannot clear.",
                },
            )
            created += 1
            conn.execute(
                sa.text(
                    "INSERT INTO mirror_sessions (id, workspace_id, device_id, requested_by, "
                    "decided_by, status, requested_at, decided_at, ends_at, ended_at, consent_at, reason) "
                    "VALUES (gen_random_uuid(), :w, :d, :by, :dec, 'ended', :req, :dec_at, :ends, "
                    ":ended, :consent, :r)"
                ),
                {
                    "w": ws_id,
                    "d": active_device,
                    "by": str(requester[0]),
                    "dec": str(owner[0]),
                    "req": now - datetime.timedelta(days=3),
                    "dec_at": now - datetime.timedelta(days=3) + datetime.timedelta(minutes=4),
                    "ends": now - datetime.timedelta(days=3) + datetime.timedelta(minutes=34),
                    "ended": now - datetime.timedelta(days=3) + datetime.timedelta(minutes=30),
                    "consent": now - datetime.timedelta(days=30),
                    "r": "Verified the daily sync completed after the fix.",
                },
            )
            created += 1
        print(f"  mirror_sessions        +{created}")

        # ------------------------------------------------------------- menu groups
        created = 0
        for name, item_count, cover_share, perishability in MENU_GROUPS:
            exists = conn.execute(
                sa.text("SELECT 1 FROM menu_groups WHERE workspace_id = :w AND name = :n"),
                {"w": ws_id, "n": name},
            ).first()
            if exists:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO menu_groups (id, workspace_id, name, item_count, cover_share, "
                    "perishability) VALUES (gen_random_uuid(), :w, :n, :c, :s, :p)"
                ),
                {
                    "w": ws_id,
                    "n": name,
                    "c": item_count,
                    "s": cover_share,
                    "p": perishability,
                },
            )
            created += 1
        print(f"  menu_groups            +{created}")

        # ----------------------------------------------------------- reservations
        created = 0
        for location_id, _ in locations:
            for days_ago in range(0, 14):
                day = (now - datetime.timedelta(days=days_ago)).date()
                exists = conn.execute(
                    sa.text(
                        "SELECT 1 FROM reservations WHERE location_id = :l AND date = :d LIMIT 1"
                    ),
                    {"l": location_id, "d": day},
                ).first()
                if exists:
                    continue
                reserved = RNG.randint(0, 45)
                conn.execute(
                    sa.text(
                        "INSERT INTO reservations (location_id, date, reserved_count, reserved_visitors, "
                        "lead_time_days) VALUES (:l, :d, :c, :v, :lead)"
                    ),
                    {
                        "l": location_id,
                        "d": day,
                        "c": reserved,
                        "v": reserved * RNG.randint(2, 5),
                        "lead": round(RNG.uniform(0.5, 9.0), 2),
                    },
                )
                created += 1
        print(f"  reservations           +{created}")

        # ------------------------------------------------------ waste observations
        created = 0
        csv_path = (
            Path(__file__).resolve().parents[2]
            / "model"
            / "data"
            / "urban-waste"
            / "Food Tracking Device dataset.csv"
        )
        if csv_path.exists():
            with csv_path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                for index, row in enumerate(reader):
                    if index >= 600:
                        break
                    code = (row.get("City / kitchen") or "").strip()
                    if not code:
                        continue
                    raw_waste = (row.get("Waste weight sum") or "").strip().replace(",", ".")
                    waste = None
                    try:
                        waste = float(raw_waste) if raw_waste else None
                    except ValueError:
                        waste = None
                    country = code.split("/")[0].strip() if "/" in code else None
                    observed = None
                    stamp = (row.get("Timestamp") or "").strip()
                    try:
                        # Source stamps are date-only (DD/MM/YYYY), so the parsed
                        # value is a date; attach UTC to satisfy the aware-datetime
                        # lint without pretending the source carried a timezone.
                        observed = (
                            datetime.datetime.strptime(stamp, "%d/%m/%Y")
                            .replace(tzinfo=datetime.UTC)
                            .date()
                        )
                    except ValueError:
                        observed = None
                    exists = conn.execute(
                        sa.text(
                            "SELECT 1 FROM waste_observations WHERE establishment_code = :c "
                            "AND observed_on = :d LIMIT 1"
                        ),
                        {"c": code, "d": observed},
                    ).first()
                    if exists:
                        continue
                    conn.execute(
                        sa.text(
                            "INSERT INTO waste_observations (establishment_code, country, observed_on, "
                            "waste_kg, ordered_portions, source_dataset, licence) VALUES "
                            "(:c, :country, :d, :w, :p, 'urban-waste-ftd', 'CC BY 4.0')"
                        ),
                        {
                            "c": code,
                            "country": country,
                            "d": observed,
                            "w": waste,
                            "p": None,
                        },
                    )
                    created += 1
        else:
            print("  ! urban-waste CSV not found; skipped waste_observations")
        print(f"  waste_observations     +{created}")

        # -------------------------------------------------------- user preferences
        created = 0
        for index, (user_id, email, role) in enumerate(users):
            exists = conn.execute(
                sa.text("SELECT 1 FROM user_preferences WHERE user_id = :u"), {"u": user_id}
            ).first()
            if not exists:
                conn.execute(
                    sa.text(
                        "INSERT INTO user_preferences (user_id, theme, language, density, "
                        "default_location_id) VALUES (:u, :theme, 'en', :density, :loc)"
                    ),
                    {
                        "u": user_id,
                        # The platform default is dark; seeding anything else
                        # would immediately override it on hydration.
                        "theme": "dark",
                        "density": "comfortable" if index % 2 == 0 else "compact",
                        "loc": str(locations[index % len(locations)][0]),
                    },
                )
                created += 1
            exists = conn.execute(
                sa.text("SELECT 1 FROM user_notification_settings WHERE user_id = :u"),
                {"u": user_id},
            ).first()
            if not exists:
                conn.execute(
                    sa.text(
                        "INSERT INTO user_notification_settings (user_id, email_digest, push_alerts, "
                        "in_app_alerts, weekly_report) VALUES (:u, true, :push, true, :weekly)"
                    ),
                    {
                        "u": user_id,
                        "push": role in ("owner", "administrator", "operator"),
                        "weekly": role == "owner",
                    },
                )
                created += 1
        print(f"  user preferences       +{created}")

        # ----------------------------------------------------------- final counts
        print("\nRow counts now:")
        # Table names are a fixed literal tuple, never caller input, so the
        # Core ``select_from`` form is used rather than an f-string.
        for table in (
            "alerts",
            "quality_issues",
            "reports",
            "report_schedules",
            "feature_definitions",
            "pipeline_runs",
            "webhook_endpoints",
            "webhook_deliveries",
            "preparation_policies",
            "policy_simulations",
            "change_requests",
            "devices",
            "device_grants",
            "device_events",
            "mirror_sessions",
            "menu_groups",
            "reservations",
            "waste_observations",
        ):
            count = conn.execute(sa.select(sa.func.count()).select_from(sa.table(table))).scalar()
            print(f"  {table:<24} {count}")
    print("DONE")


if __name__ == "__main__":
    main()
