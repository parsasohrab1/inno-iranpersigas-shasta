"""Runtime configuration of the LCIS API (12-factor: everything overridable by environment)."""
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

# NFR-03 (TLS) is terminated by the reverse proxy in deployment; here the API serves plain HTTP and the
# docker-compose/deployment docs say so. The token secret MUST come from the environment in production;
# a random dev secret is generated once per process so that tests never share tokens across restarts.
SECRET = os.environ.get("LCIS_SECRET") or "dev-secret-" + secrets.token_hex(16)
TOKEN_ALGORITHM = "HS256"
TOKEN_TTL_MIN = int(os.environ.get("LCIS_TOKEN_TTL_MIN", "480"))
ACCESS_TOKEN_MIN_ROUNDS = 120_000          # pbkdf2 work factor for password hashing

PRODUCT_VERSION = "lcis-trl5-0.4.0"
DEFAULT_ADMIN = ("admin", "admin123")      # created by the seed; must be changed in production (docs say so)
