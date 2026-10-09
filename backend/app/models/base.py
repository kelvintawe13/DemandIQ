"""Shared SQLAlchemy base and helpers."""

from __future__ import annotations

import uuid

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Naming convention helps Alembic generate deterministic constraint names.
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)

# SQLAlchemy compares column values by object identity when deciding whether an
# attribute is dirty. A dict loaded from a JSONB column is therefore *not*
# written back after an in-place edit (``row.preview["k"] = v``) — the change is
# silently dropped, which is how the file-mapping step used to lose its column
# map and make ``/ingest/file/{id}/commit`` fail with 422 ``missing_mapping``.
#
# ``MutableDict`` installs a listener that marks the attribute dirty for
# top-level mutations (``[]=``, ``update``, ``pop``, ``del``). The emitted DDL
# is unchanged — it is still ``JSONB`` — so no migration is required. Every
# JSONB column must use this alias.
#
# Note: only top-level mutation is tracked. Replacing the value (or rebuilding
# it, e.g. ``row.data = {**row.data, "k": v}``) always works; mutating a nested
# list/dict in place still does not, so copy the nested value on the way out.
MutableJSONB = MutableDict.as_mutable(JSONB)
# Same tracking for columns whose value is a JSON *list* — ``MutableDict``
# only accepts mappings, and a bare JSONB on a list column drops in-place
# edits exactly like the dict case. Owned by the signup profile for now.
MutableJSONList = MutableList.as_mutable(JSONB)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = metadata


def uuid_pk() -> Mapped[uuid.UUID]:
    """Helper for UUID primary keys with ``gen_random_uuid`` default."""
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
