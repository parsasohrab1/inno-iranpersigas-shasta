"""FR-10 audit middleware re-export: core.audit holds the implementation, main.py mounts it."""
from ..core.audit import AuditMiddleware  # noqa: F401
