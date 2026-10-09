"""Password hashing helpers (bcrypt via passlib)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ``bcrypt`` is the approved algorithm per IMPLEMENTATION_GUIDE.md.
# Use ``bcrypt`` directly for hashing to avoid the passlib+bcrypt>=4.0
# ``detect_wrap_bug`` incompatibility (ValueError on >72 byte secrets).
# Fall back to passlib if bcrypt is unavailable.


def hash_password(plain: str) -> str:
    """Hash a plaintext password.

    Args:
        plain: Raw password string.

    Returns:
        Bcrypt hash string.
    """
    try:
        import bcrypt

        salt = bcrypt.gensalt()
        # bcrypt limits to 72 bytes; truncate as passlib does internally.
        return bcrypt.hashpw(plain.encode("utf-8")[:72], salt).decode("utf-8")
    except Exception:
        from passlib.context import CryptContext

        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return str(ctx.hash(plain))


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a hash.

    Args:
        plain: Candidate password.
        hashed: Stored hash.

    Returns:
        True if the password matches.
    """
    try:
        import bcrypt

        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Password verification via bcrypt failed: %s", exc)
        try:
            from passlib.context import CryptContext

            ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
            return bool(ctx.verify(plain, hashed))
        except Exception as exc2:  # pragma: no cover
            logger.warning("Password verification failed: %s", exc2)
            return False


def needs_rehash(hashed: str) -> bool:
    """Check whether a stored hash should be re-hashed."""
    try:
        from passlib.context import CryptContext

        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return bool(ctx.needs_update(hashed))
    except Exception:
        return False
