"""Quality detection — turns the five configured rules into real issues.

The Quality Monitor used to score a workspace from whatever rows were in
``quality_issues``, and nothing in the application ever created any: only the
seed scripts did. This module closes that loop. :func:`commit_demand` calls
:func:`evaluate_after_commit` after every write, so the score reflects the
workspace's own data instead of seeded rows.

Two properties matter more than the checks themselves:

* **Ingestion never fails because a check did.** :func:`evaluate_after_commit`
  swallows and logs, exactly like the realtime fan-out and the conformal update
  in the same commit path.
* **Issues are idempotent.** A finding is keyed by
  ``(rule, location, summary)``: the same problem found twice does not create a
  second row, and a problem that has cleared closes its issue rather than
  leaving it open forever.

The rule names are the ones ``GET /api/v1/quality/rules`` already seeds, so the
detector and the screen cannot drift apart.
"""

from __future__ import annotations

import datetime
import logging
import statistics
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.location import Connector, DemandObservation, IngestUpload
from app.models.quality import QualityIssue, QualityRule

logger = logging.getLogger(__name__)

#: name -> description, matching the defaults seeded by the quality router.
DEFAULT_RULES: dict[str, str] = {
    "gap_detection": "Detects gaps in the demand series",
    "spike_detection": "Flags implausible spikes",
    "duplicate_conflict": "Duplicate-day conflicts across channels",
    "stale_connector": "Connector freshness",
    "schema_drift": "Schema drift in uploads",
}

#: How far back the location checks look. Bounded so a commit stays cheap on a
#: location with years of history.
LOOKBACK_DAYS = 90

#: A connector that has not pulled for this long is stale.
CONNECTOR_STALE_DAYS = 7

#: An upload that failed this far back is no longer news.
UPLOAD_LOOKBACK_DAYS = 30

#: A value this many times the trailing median, and at least this large, is a
#: spike. Both guards matter: a café that sells 1–3 portions a day would trip a
#: pure ratio test on ordinary noise.
SPIKE_RATIO = 3.0
SPIKE_MIN_VALUE = 20


@dataclass(frozen=True, slots=True)
class Finding:
    """One detected problem, ready to become a :class:`QualityIssue`."""

    rule: str
    severity: str
    summary: str
    suggested_fix: str | None = None
    location_id: uuid.UUID | None = None


def issue_key(
    rule_id: uuid.UUID | None, location_id: uuid.UUID | None, summary: str
) -> tuple[uuid.UUID | None, uuid.UUID | None, str]:
    """Identity of an open issue, used to update rather than duplicate it.

    A location rule holds **one** open issue per location. Its summary carries
    the current numbers — a gap that grew, a newer spike — so keying on the text
    would file a second issue for the same problem after the next commit. A
    workspace rule has no location to key on, so there the summary is the
    identity: two failed uploads are two separate problems.
    """
    if location_id is not None:
        return (rule_id, location_id, "")
    return (rule_id, None, summary)


# --------------------------------------------------------------------------- #
# Pure checks — no database, so they are testable on their own.
# --------------------------------------------------------------------------- #


def find_gap_finding(
    dates: list[datetime.date], *, today: datetime.date | None = None
) -> Finding | None:
    """Report missing days in the recent part of a demand series.

    Closure days are a normal business fact, so a single missing day is ``info``;
    three or more in the window is a ``warning``. The window is the last 30 days
    of the series (or everything, when the series is shorter), because an old
    gap that was already dealt with should not keep the score down.
    """
    if len(dates) < 2:
        return None
    ordered = sorted(set(dates))
    anchor = today or ordered[-1]
    window_start = anchor - datetime.timedelta(days=29)
    recent = [d for d in ordered if d >= window_start]
    if len(recent) < 2:
        return None
    span = [recent[0] + datetime.timedelta(days=i) for i in range((recent[-1] - recent[0]).days + 1)]
    missing = [d for d in span if d not in set(recent)]
    if not missing:
        return None
    severity = "warning" if len(missing) >= 3 else "info"
    first = missing[0].isoformat()
    return Finding(
        rule="gap_detection",
        severity=severity,
        summary=f"{len(missing)} day(s) missing from the demand series, first {first}",
        suggested_fix="Fill the missing days, or confirm the business was closed.",
    )


def find_spike_finding(values: list[int]) -> Finding | None:
    """Flag a last value that is implausibly above its own recent history.

    Compares against the median of the seven values before it, not the mean:
    one bad Tuesday should not make the next Thursday look normal.
    """
    if len(values) < 5:
        return None
    latest = values[-1]
    history = values[-8:-1]
    if not history:
        return None
    median = statistics.median(history)
    if median <= 0:
        return None
    if latest >= SPIKE_MIN_VALUE and latest >= SPIKE_RATIO * median:
        return Finding(
            rule="spike_detection",
            severity="warning",
            summary=(
                f"latest value {latest:,} is {latest / median:.1f}x the trailing median "
                f"of {median:,.0f}"
            ),
            suggested_fix="Check the entry against the day's log before relying on it.",
        )
    return None


def find_overwrite_finding(previous: int | None, new: int, day: datetime.date) -> Finding | None:
    """Flag a day rewritten with a different value by another channel.

    The database enforces one row per (location, date), so the only thing a
    second channel can do is overwrite. That is legitimate reconciliation — but
    a silent change of a day's sales figure is exactly what a monitor exists to
    surface.
    """
    if previous is None or previous == new:
        return None
    return Finding(
        rule="duplicate_conflict",
        severity="info",
        summary=f"{day.isoformat()} overwritten: {previous:,} -> {new:,} by a later channel",
        suggested_fix="Confirm the later source is the correct record for that day.",
    )


# --------------------------------------------------------------------------- #
# Database work
# --------------------------------------------------------------------------- #


def _rules_by_name(db: Session, workspace_id: uuid.UUID) -> dict[str, QualityRule]:
    """Existing rules for the workspace, creating any that are missing."""
    rows = list(
        db.scalars(select(QualityRule).where(QualityRule.workspace_id == workspace_id)).all()
    )
    by_name = {r.name: r for r in rows}
    for name, description in DEFAULT_RULES.items():
        if name not in by_name:
            rule = QualityRule(
                workspace_id=workspace_id,
                name=name,
                description=description,
                enabled=True,
            )
            db.add(rule)
            db.flush()
            by_name[name] = rule
    return by_name


def _location_findings(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    day: datetime.date,
    previous_qty: int | None,
    new_qty: int,
) -> list[Finding]:
    """Gaps, spikes and overwrites for the location that was just written."""
    rows = list(
        db.scalars(
            select(DemandObservation)
            .where(
                DemandObservation.workspace_id == workspace_id,
                DemandObservation.location_id == location_id,
                DemandObservation.date >= day - datetime.timedelta(days=LOOKBACK_DAYS),
            )
            .order_by(DemandObservation.date)
        ).all()
    )
    dates = [r.date for r in rows]
    values = [r.demand_qty for r in rows]

    findings: list[Finding] = []
    for found in (
        find_gap_finding(dates),
        find_spike_finding(values),
        find_overwrite_finding(previous_qty, new_qty, day),
    ):
        if found is not None:
            findings.append(
                Finding(
                    rule=found.rule,
                    severity=found.severity,
                    summary=found.summary,
                    suggested_fix=found.suggested_fix,
                    location_id=location_id,
                )
            )
    return findings


def _workspace_findings(
    db: Session, *, workspace_id: uuid.UUID, now: datetime.datetime
) -> list[Finding]:
    """Stale connectors and uploads that failed or dropped rows."""
    findings: list[Finding] = []

    connectors = list(
        db.scalars(
            select(Connector).where(
                Connector.workspace_id == workspace_id,
                Connector.status == "active",
            )
        ).all()
    )
    stale_before = now - datetime.timedelta(days=CONNECTOR_STALE_DAYS)
    for c in connectors:
        last = c.last_sync_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=datetime.UTC)
        if last is None or last < stale_before:
            when = "never" if last is None else last.date().isoformat()
            findings.append(
                Finding(
                    rule="stale_connector",
                    severity="warning",
                    summary=f"{c.name} last pulled {when}; expected within {CONNECTOR_STALE_DAYS} days",
                    suggested_fix="Run a pull, or pause the connector if it is retired.",
                )
            )

    uploads = list(
        db.scalars(
            select(IngestUpload).where(
                IngestUpload.workspace_id == workspace_id,
                IngestUpload.created_at >= now - datetime.timedelta(days=UPLOAD_LOOKBACK_DAYS),
            )
        ).all()
    )
    for u in uploads:
        if u.status == "failed" or (u.error_count or 0) > 0:
            detail = "failed" if u.status == "failed" else f"{u.error_count} row(s) rejected"
            findings.append(
                Finding(
                    rule="schema_drift",
                    severity="warning",
                    summary=f"{u.filename}: {detail}",
                    suggested_fix="Re-map the columns and re-import the rejected rows.",
                )
            )
    return findings


def record_findings(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    findings: list[Finding],
    evaluated_location_id: uuid.UUID | None = None,
) -> list[QualityIssue]:
    """Persist findings, keeping one open issue per (rule, location, summary).

    ``evaluated_location_id`` bounds what may be *closed*: this evaluation only
    looked at one location, so an issue on another location must not be resolved
    by an unrelated write.
    """
    rules = _rules_by_name(db, workspace_id)
    open_issues = list(
        db.scalars(
            select(QualityIssue).where(
                QualityIssue.workspace_id == workspace_id,
                QualityIssue.resolved_at.is_(None),
                QualityIssue.dismissed_at.is_(None),
            )
        ).all()
    )
    existing = {
        issue_key(i.rule_id, i.location_id, i.summary): i
        for i in open_issues
        if i.rule_id is not None
    }

    current: set[tuple[uuid.UUID | None, uuid.UUID | None, str]] = set()
    written: list[QualityIssue] = []
    for f in findings:
        rule = rules.get(f.rule)
        if rule is None or not rule.enabled:
            continue
        key = issue_key(rule.id, f.location_id, f.summary)
        current.add(key)
        issue = existing.get(key)
        if issue is None:
            issue = QualityIssue(
                workspace_id=workspace_id,
                rule_id=rule.id,
                location_id=f.location_id,
                severity=f.severity,
                summary=f.summary,
                suggested_fix=f.suggested_fix,
            )
            db.add(issue)
            written.append(issue)
        else:
            issue.summary = f.summary
            issue.severity = f.severity
            issue.suggested_fix = f.suggested_fix

    # Close what this evaluation would no longer raise: a fixed gap or a
    # connector that pulled again should not keep the score down.
    for key, issue in existing.items():
        rule_id, issue_location, _summary = key
        observable = issue_location is None or issue_location == evaluated_location_id
        if observable and any(r.id == rule_id for r in rules.values()) and key not in current:
            issue.resolved_at = datetime.datetime.now(datetime.UTC)

    db.flush()
    return written


def evaluate_after_commit(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    day: datetime.date,
    previous_qty: int | None,
    new_qty: int,
) -> list[QualityIssue]:
    """Run every rule after a demand row is committed.

    Best-effort by contract: a failure here is logged, never raised, because the
    demand row has already been written and rejecting the request would lose it.
    """
    try:
        now = datetime.datetime.now(datetime.UTC)
        findings = _location_findings(
            db,
            workspace_id=workspace_id,
            location_id=location_id,
            day=day,
            previous_qty=previous_qty,
            new_qty=new_qty,
        )
        findings.extend(_workspace_findings(db, workspace_id=workspace_id, now=now))
        written = record_findings(
            db,
            workspace_id=workspace_id,
            findings=findings,
            evaluated_location_id=location_id,
        )
        if written:
            logger.info("quality issues recorded workspace=%s count=%s", workspace_id, len(written))
        return written
    except Exception:
        logger.warning(
            "quality evaluation failed workspace=%s location=%s", workspace_id, location_id,
            exc_info=True,
        )
        return []
