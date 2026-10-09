"""Connector pull adapter — fetch demand from a provider and commit it.

This is the half of ``connector_sync`` that was missing. Without it the job
could only stamp ``last_sync_at`` and count observations other channels had
already written (a device push, a file import), so a queued sync reported
"rows arrived" for work it never did. A channel that never contacts its
provider is a channel that cannot ingest, and saying otherwise is how the
screen came to show demand that had not been pulled.

The adapter is deliberately provider-agnostic and boring:

1. Read the connector's pull endpoint from ``credentials``.
2. ``GET`` it (with ``?since=<last_sync_at>`` for an incremental pull).
3. Map each returned row to the canonical schema — through the connector's
   ``connector_mappings`` when one exists, otherwise accepting canonical field
   names directly.
4. Commit every valid row through :func:`~app.services.ingestion.normaliser.commit_demand`,
   the single writer of ``demand_observations``, tagged with ``source_id`` so
   the row stays attributable to this channel.

Rows that cannot be mapped count as *skipped*, with the reason recorded; the
adapter never invents a row to make a pull look successful. A transport error
is not swallowed — it propagates so the run fails and raises an alert, which is
the only signal an operator gets that a channel is down.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.models.connector_mapping import ConnectorMapping
from app.models.location import Connector
from app.services.ingestion.normaliser import (
    NormaliserError,
    commit_demand,
    validate_demand_row,
)

logger = logging.getLogger(__name__)

# A provider pull is a background job, not a request a person is waiting on,
# but it still must not hang a worker forever.
PULL_TIMEOUT_SECONDS = 15.0

# Where the pull URL may live inside ``connectors.credentials``. Any one of
# these is accepted so a provider can keep the name its own console uses.
_ENDPOINT_KEYS = ("endpoint", "base_url", "url", "pull_url")

CANONICAL_FIELDS = ("location_id", "date", "demand_qty", "notes")


@dataclass(frozen=True, slots=True)
class PullOutcome:
    """What a single connector pull did.

    Attributes:
        configured: Whether the connector has a pull endpoint at all. An
            unconfigured channel is *not* treated as a completed sync — the
            caller must not stamp freshness for it.
        pulled: Rows the provider returned (before validation).
        committed: Rows written or reconciled through ``commit_demand``. Always
            ``0`` on a dry run.
        skipped: Rows the provider returned that could not be committed.
        detail: One-line, human-readable summary for the ``pipeline_runs`` row.
        rows: The mapped rows that passed validation, in order. Populated in
            both modes so a dry run can preview them.
        validated: ``len(rows)`` — rows that would commit (dry run) or did.
        errors: Per-row failure reasons, bounded to the first few.
    """

    configured: bool
    pulled: int
    committed: int
    skipped: int
    detail: str
    rows: tuple[dict[str, Any], ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def validated(self) -> int:
        """Rows that passed mapping and validation."""
        return len(self.rows)

    @property
    def persisted_detail(self) -> str:
        """Detail for the connector's stored ``last_pull_detail``.

        Appends the first row failure so "skipped 2" on the Sources page comes
        with at least one concrete reason to act on; the full list is returned
        by the pull response.
        """
        if self.errors:
            return f"{self.detail} — first error: {self.errors[0]}"
        return self.detail


# ---------------------------------------------------------------------------
# Credential / endpoint helpers
# ---------------------------------------------------------------------------


def endpoint_of(conn: Connector) -> str | None:
    """Return the configured pull URL, or ``None`` when there is not one."""
    creds = conn.credentials if isinstance(conn.credentials, dict) else {}
    for key in _ENDPOINT_KEYS:
        value = creds.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _linked_location_ids(conn: Connector) -> list[str]:
    """Location ids merged into credentials by the connectors router."""
    creds = conn.credentials if isinstance(conn.credentials, dict) else {}
    raw = creds.get("location_ids")
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _headers_for(conn: Connector) -> dict[str, str]:
    """Build request headers from the connector's credential bag.

    Supports an explicit ``headers`` object, a prebuilt ``auth_header``, and the
    common ``token`` / ``api_key`` pair (sent as a bearer token). Nothing here
    logs or echoes the secret.
    """
    creds = conn.credentials if isinstance(conn.credentials, dict) else {}
    headers: dict[str, str] = {"Accept": "application/json"}
    extra = creds.get("headers")
    if isinstance(extra, dict):
        headers.update({str(k): str(v) for k, v in extra.items()})
    auth = creds.get("auth_header")
    if isinstance(auth, str) and auth.strip():
        headers["Authorization"] = auth.strip()
    else:
        token = creds.get("token") or creds.get("api_key")
        if isinstance(token, str) and token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _fetch_payload(
    endpoint: str,
    headers: dict[str, str],
    *,
    since: datetime.datetime | None = None,
    http_client: httpx.Client | None = None,
) -> Any:
    """GET the endpoint and return decoded JSON.

    Isolated in one function so tests can substitute a payload without a live
    provider (see ``tests/test_connector_pull.py``), the same way webhook
    delivery takes an injectable ``httpx.Client``.
    """
    params: dict[str, str] = {}
    if since is not None:
        params["since"] = since.isoformat()
    client = http_client or httpx.Client(timeout=PULL_TIMEOUT_SECONDS)
    close_client = http_client is None
    try:
        resp = client.get(endpoint, headers=headers, params=params or None)
        resp.raise_for_status()
        return resp.json()
    finally:
        if close_client:
            client.close()


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Accept ``{"rows": [...]}``, ``{"data": [...]}`` or a bare list."""
    if isinstance(payload, list):
        raw_rows = payload
    elif isinstance(payload, dict):
        for key in ("rows", "data", "demand"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                raw_rows = candidate
                break
        else:
            return []
    else:
        return []
    return [row for row in raw_rows if isinstance(row, dict)]


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def _source_for(mappings: list[ConnectorMapping]) -> dict[str, str]:
    """Map canonical field -> the provider's field name for it."""
    return {m.canonical_field: m.source_field for m in mappings if isinstance(m, ConnectorMapping)}


def _transform_for(mappings: list[ConnectorMapping]) -> dict[str, str]:
    """Map canonical field -> declared transform, if any."""
    return {
        m.canonical_field: (m.transform or "") for m in mappings if isinstance(m, ConnectorMapping)
    }


def _pick(raw: dict[str, Any], field: str, source_for: dict[str, str]) -> Any:
    """Read a canonical field from a provider row, honouring its mapping."""
    if field in source_for and source_for[field] in raw:
        return raw[source_for[field]]
    return raw.get(field)


def _coerce(field: str, value: Any, transform: str) -> Any:
    """Apply an optional mapping transform, then normalise by canonical field."""
    kind = (transform or "").strip().lower()
    if kind in ("int", "integer"):
        value = int(str(value).strip())
    elif kind in ("float", "number"):
        value = float(str(value).strip())
    elif kind in ("date", "iso_date"):
        value = _parse_date(value)
    elif kind in ("string", "str", "text"):
        value = str(value).strip()

    if field == "date":
        return _parse_date(value)
    if field == "demand_qty":
        return int(str(value).strip())
    if field == "location_id":
        return uuid.UUID(str(value).strip())
    if field == "notes":
        return None if value is None else str(value)
    return value


def _parse_date(value: Any) -> datetime.date:
    """Parse a provider date — ISO string, ``date`` or ``datetime``."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value).strip()[:10])


def canonical_row(
    raw: dict[str, Any],
    source_for: dict[str, str],
    transforms: dict[str, str],
    default_location_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Turn one provider row into a canonical commit payload.

    Raises:
        ValueError: when a required field (date, demand_qty, location_id) is
            missing or malformed. The caller records it as a skipped row.
    """
    date_raw = _pick(raw, "date", source_for)
    qty_raw = _pick(raw, "demand_qty", source_for)
    if date_raw is None or (isinstance(date_raw, str) and not date_raw.strip()):
        raise ValueError("row has no date")
    if qty_raw is None or (isinstance(qty_raw, str) and not qty_raw.strip()):
        raise ValueError("row has no demand_qty")

    loc_raw = _pick(raw, "location_id", source_for)
    if loc_raw is None or (isinstance(loc_raw, str) and not loc_raw.strip()):
        if default_location_id is None:
            raise ValueError("row has no location_id and the connector links no single location")
        location_id = default_location_id
    else:
        location_id = _coerce("location_id", loc_raw, transforms.get("location_id", ""))

    return {
        "location_id": location_id,
        "date": _coerce("date", date_raw, transforms.get("date", "")),
        "demand_qty": _coerce("demand_qty", qty_raw, transforms.get("demand_qty", "")),
        "notes": _coerce("notes", _pick(raw, "notes", source_for), transforms.get("notes", "")),
    }


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------


def pull_connector(
    db: Any,
    conn: Connector,
    *,
    mappings: list[ConnectorMapping] | None = None,
    default_location_id: uuid.UUID | None = None,
    http_client: httpx.Client | None = None,
    dry_run: bool = False,
) -> PullOutcome:
    """Fetch a connector's demand from its provider and commit the valid rows.

    Args:
        db: Active SQLAlchemy session (caller owns commit/rollback).
        conn: The connector to pull.
        mappings: Field mappings for this connector; read from the DB when
            omitted.
        default_location_id: Location for rows that carry none — normally the
            connector's single linked location.
        http_client: Optional client for testing.
        dry_run: When ``True``, fetch and map but write nothing — the rows are
            returned in :attr:`PullOutcome.rows` for preview.

    Returns:
        :class:`PullOutcome`. ``configured`` is ``False`` (and nothing is
        fetched or stamped) when the connector has no endpoint.

    Raises:
        httpx.HTTPError: on a transport/HTTP failure, so the run fails loudly.
    """
    endpoint = endpoint_of(conn)
    if endpoint is None:
        return PullOutcome(
            configured=False,
            pulled=0,
            committed=0,
            skipped=0,
            detail="no pull endpoint configured — nothing fetched",
        )

    if mappings is None:
        mappings = _load_mappings(db, conn)
    source_for = _source_for(mappings)
    transforms = _transform_for(mappings)

    if default_location_id is None:
        linked = _linked_location_ids(conn)
        if len(linked) == 1:
            try:
                default_location_id = uuid.UUID(linked[0])
            except ValueError:
                default_location_id = None

    payload = _fetch_payload(
        endpoint,
        _headers_for(conn),
        since=getattr(conn, "last_sync_at", None),
        http_client=http_client,
    )
    raw_rows = _rows_from_payload(payload)

    committed = 0
    skipped = 0
    errors: list[str] = []
    accepted: list[dict[str, Any]] = []
    for raw in raw_rows:
        try:
            row = canonical_row(raw, source_for, transforms, default_location_id)
            if dry_run:
                # Same rules the real commit applies, minus the write.
                validate_demand_row(
                    db,
                    workspace_id=conn.workspace_id,
                    location_id=row["location_id"],
                    date=row["date"],
                    demand_qty=row["demand_qty"],
                )
            else:
                commit_demand(
                    db,
                    workspace_id=conn.workspace_id,
                    location_id=row["location_id"],
                    date=row["date"],
                    demand_qty=row["demand_qty"],
                    source=conn.type,
                    source_id=conn.id,
                    notes=row["notes"],
                )
                committed += 1
            accepted.append(row)
        except (NormaliserError, ValueError, TypeError, KeyError) as exc:
            skipped += 1
            if len(errors) < 5:
                errors.append(str(exc))

    if dry_run:
        detail = (
            f"preview: {len(accepted)} of {len(raw_rows)} row(s) ready to commit"
            if raw_rows
            else "preview: the provider returned no rows"
        )
    else:
        detail = (
            f"pulled {len(raw_rows)} row(s): committed {committed}"
            if raw_rows
            else "pulled 0 row(s) from the provider"
        )
    if skipped:
        detail += f", skipped {skipped}"
    return PullOutcome(
        configured=True,
        pulled=len(raw_rows),
        committed=committed,
        skipped=skipped,
        detail=detail,
        rows=tuple(accepted),
        errors=tuple(errors),
    )


def _load_mappings(db: Any, conn: Connector) -> list[ConnectorMapping]:
    """Read this connector's mappings, tolerating a mocked/limited session."""
    try:
        from sqlalchemy import select

        rows = db.scalars(
            select(ConnectorMapping).where(ConnectorMapping.connector_id == conn.id)
        ).all()
    except Exception:
        return []
    return [r for r in rows if isinstance(r, ConnectorMapping)]
