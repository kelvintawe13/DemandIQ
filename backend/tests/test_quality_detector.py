"""Quality detection: the five checks and the commit hook.

The checks are pure functions, so they are tested directly; the commit hook is
tested for the property that matters operationally — a failing check must never
break an ingestion that has already written its row.
"""

from __future__ import annotations

import datetime
import uuid

from app.services.quality import detector


def test_gap_check_flags_missing_days():
    start = datetime.date(2026, 1, 1)
    dates = [start + datetime.timedelta(days=i) for i in range(10)]
    dates.remove(start + datetime.timedelta(days=4))
    dates.remove(start + datetime.timedelta(days=5))

    finding = detector.find_gap_finding(dates)

    assert finding is not None
    assert finding.rule == "gap_detection"
    assert "2 day(s) missing" in finding.summary
    assert finding.severity == "info"


def test_gap_check_is_quiet_on_a_complete_series():
    start = datetime.date(2026, 1, 1)
    dates = [start + datetime.timedelta(days=i) for i in range(20)]
    assert detector.find_gap_finding(dates) is None


def test_gap_check_warns_at_three_missing_days():
    start = datetime.date(2026, 1, 1)
    dates = [start + datetime.timedelta(days=i) for i in range(12)]
    for offset in (3, 4, 5):
        dates.remove(start + datetime.timedelta(days=offset))

    finding = detector.find_gap_finding(dates)

    assert finding is not None
    assert finding.severity == "warning"


def test_spike_check_flags_triple_the_trailing_median():
    finding = detector.find_spike_finding([10, 10, 12, 9, 11, 10, 10, 45])

    assert finding is not None
    assert finding.rule == "spike_detection"
    assert finding.severity == "warning"


def test_spike_check_ignores_normal_movement():
    assert detector.find_spike_finding([10, 10, 12, 9, 11, 10, 10, 14]) is None


def test_spike_check_ignores_a_triple_on_a_tiny_base():
    # 3x the median, but nine visitors is ordinary noise for a small café.
    assert detector.find_spike_finding([3, 3, 3, 3, 3, 3, 3, 9]) is None


def test_overwrite_check_reports_a_changed_day():
    finding = detector.find_overwrite_finding(
        80, 92, datetime.date(2026, 1, 5)
    )
    assert finding is not None
    assert finding.rule == "duplicate_conflict"
    assert "80 -> 92" in finding.summary


def test_overwrite_check_is_quiet_when_the_value_is_unchanged():
    day = datetime.date(2026, 1, 5)
    assert detector.find_overwrite_finding(80, 80, day) is None
    assert detector.find_overwrite_finding(None, 80, day) is None


def test_evaluate_after_commit_never_raises_when_the_database_fails():
    """The demand row is already written; a broken check must not undo it."""

    class BrokenSession:
        def scalars(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    written = detector.evaluate_after_commit(
        BrokenSession(),  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        location_id=uuid.uuid4(),
        day=datetime.date(2026, 1, 5),
        previous_qty=None,
        new_qty=42,
    )

    assert written == []


def test_default_rules_match_the_names_the_screen_seeds():
    assert set(detector.DEFAULT_RULES) == {
        "gap_detection",
        "spike_detection",
        "duplicate_conflict",
        "stale_connector",
        "schema_drift",
    }


def test_a_location_rule_keeps_one_open_issue_per_location():
    """A newer spike must update the issue, not file a second one."""
    rule_id = uuid.uuid4()
    location_id = uuid.uuid4()

    first = detector.issue_key(rule_id, location_id, "latest value 200 is 20.0x the median")
    second = detector.issue_key(rule_id, location_id, "latest value 210 is 21.0x the median")

    assert first == second
    assert first != detector.issue_key(rule_id, uuid.uuid4(), "latest value 210 is 21.0x the median")


def test_a_workspace_rule_keeps_distinct_issues_per_finding():
    """Two failed uploads are two problems, not one rewritten row."""
    rule_id = uuid.uuid4()

    assert detector.issue_key(rule_id, None, "a.csv: failed") != detector.issue_key(
        rule_id, None, "b.csv: failed"
    )
