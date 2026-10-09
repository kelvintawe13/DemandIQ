"""SQLAlchemy engine, session factory and FastAPI dependency.

Uses *sync* SQLAlchemy 2.0 with ``psycopg2`` for simplicity, matching
``IMPLEMENTATION_GUIDE.md``. Async can be introduced later without changing
the model definitions.

The module is import-safe: engine creation is lazy so that importing the
package does not require a live database (needed for ``pip install -e`` and
unit tests that mock the session).
"""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

# The declarative base and its naming-convention ``MetaData`` live in
# ``app.models.base``; re-exported here because ``app.database.Base`` is the
# long-standing import path used by Alembic's ``env.py`` and scripts. Do NOT
# create a second base with ``declarative_base()`` here: two registries mean
# ``init_db()`` — and anything else reaching for this metadata — silently sees
# an empty schema.
from app.models.base import Base

logger = logging.getLogger(__name__)

# These are initialised lazily; ``get_engine`` / ``get_session_factory``
# create them on first use.


_engine = None  # type: ignore[assignment]
_SessionLocal = None  # type: ignore[assignment]


def get_engine() -> Engine:
    """Return the global SQLAlchemy engine, creating it if needed."""
    global _engine
    if _engine is None:
        cfg = get_settings()
        _engine = create_engine(
            cfg.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            echo=cfg.debug,
        )
        logger.info(
            "Database engine created url=%s", _engine.url.render_as_string(hide_password=True)
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the global session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


# Backwards-compatible aliases used throughout the codebase.
def _lazy_engine_proxy():  # type: ignore[no-untyped-def]
    return get_engine()


# Expose as module-level attributes for Alembic ``env.py`` which expects
# ``app.database.Base`` and ``app.database.engine``.
engine = _lazy_engine_proxy  # type: ignore[assignment]


def get_session_local() -> sessionmaker[Session]:
    """Public accessor for the session factory (useful in tests)."""
    return get_session_factory()


# Concrete ``SessionLocal`` used by dependency injection.
# It is a ``sessionmaker`` callable, not an instance.
SessionLocal = get_session_factory()  # type: ignore[assignment]


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session.

    Yields:
        Session: SQLAlchemy session bound to the configured engine.

    The session is closed automatically after the request, and rolled back
    on unhandled exceptions.
    """
    factory = get_session_factory()
    db: Session = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Import all models and create tables (for dev / tests only).

    Production must use Alembic migrations. This helper is convenient for
    ephemeral test databases.
    """
    # Import models so they register with ``Base.metadata``.
    import app.models  # noqa: F401  # pylint: disable=unused-import

    if not Base.metadata.tables:
        raise RuntimeError("ORM metadata is empty; models were not imported")

    Base.metadata.create_all(bind=get_engine())
    logger.info("Database tables created via Base.metadata (%d tables)", len(Base.metadata.tables))
