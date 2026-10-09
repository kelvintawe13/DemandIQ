"""Re-export shared dependencies for router imports."""

from app.database import get_db
from app.security.deps import get_current_user, get_current_user_optional, require_role

__all__ = ["get_current_user", "get_current_user_optional", "get_db", "require_role"]
