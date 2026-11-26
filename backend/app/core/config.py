"""Runtime configuration of the LCIS API (12-factor: everything overridable by environment)."""
import hashlib
import logging
import os
import secrets

# Product root = the directory that contains backend/, data/, validation/ (two levels up from app/core).
PRODUCT_ROOT = os.environ.get(
    "LCIS_PRODUCT_ROOT",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)
DATA_DIR = os.path.join(PRODUCT_ROOT, "data")
EIA_DIR = os.path.join(DATA_DIR, "eia")
VALIDATION_DIR = os.path.join(PRODUCT_ROOT, "validation", "results")
ML_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "ml")
DB_PATH = os.environ.get("LCIS_DB_PATH", os.path.join(ML_DIR, "lcis.db"))

# NFR-03 (TLS) is terminated by the reverse proxy in deployment; the API itself serves plain HTTP.
# D-01: a published default secret is refused. Production must set LCIS_SECRET; development gets a
# process-local random secret so tokens are never signed with a value that lives in the repository.
ENV = os.environ.get("LCIS_ENV", "development").strip().lower()
_secret = os.environ.get("LCIS_SECRET")
if ENV == "production":
    if not _secret or len(_secret) < 32:
        raise RuntimeError(
            "LCIS_SECRET must be a random string of at least 32 characters when LCIS_ENV=production"
        )
    SECRET = _secret
else:
    SECRET = _secret or ("dev-secret-" + secrets.token_hex(16))
    if not _secret:
        logging.getLogger("lcis.config").warning(
            "LCIS_SECRET is unset; this process uses a random development secret that will not survive a restart"
        )

# D-19: the audit chain uses a key that is not the raw JWT secret. LCIS_AUDIT_KEY overrides it;
# otherwise the key is a domain-separated digest of the token secret.
_audit_key = os.environ.get("LCIS_AUDIT_KEY") or ""
AUDIT_KEY = hashlib.sha256(b"lcis-audit-v1\0" + (_audit_key or SECRET).encode()).digest()

TOKEN_ALGORITHM = "HS256"
TOKEN_TTL_MIN = int(os.environ.get("LCIS_TOKEN_TTL_MIN", "480"))
ACCESS_TOKEN_MIN_ROUNDS = 120_000          # pbkdf2 work factor for password hashing
MIN_PASSWORD_LEN = 10

PRODUCT_VERSION = "lcis-trl5-0.5.0"
DEFAULT_ADMIN = ("admin", "admin123")      # development seed only; /api/seed is refused in production
