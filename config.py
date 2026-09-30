"""
All environment-driven configuration lives here — one place to change
deployment settings without touching application code, same principle as
the in-app Pricing & System Settings page (that one's for money/business
settings a hospital owner changes; this one's for infra/ops settings a
system administrator changes).

Reads from a `.env` file in the project root if one exists (see
`.env.example` for every variable this app recognizes), falling back to
real environment variables, falling back to the defaults below. Nothing
sensitive should ever be committed to source control — `.env` is meant
to be local-only (make sure it's in .gitignore).
"""
import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


class Config:
    # --- core Flask ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production-please")
    DEBUG = _env_bool("FLASK_DEBUG", False)
    TESTING = False

    # --- database ---
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'hospital.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- sessions / cookies ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Only send the session cookie over HTTPS. Leave False for local/Termux
    # http:// testing; set true once this is served behind real TLS, or
    # logins will silently fail (the browser won't send the cookie back).
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    # Auto-logout idle shared hospital terminals.
    PERMANENT_SESSION_LIFETIME = _env_int("SESSION_TIMEOUT_MINUTES", 20) * 60

    # --- forms / CSRF ---
    WTF_CSRF_TIME_LIMIT = None

    # --- request size guard (protects against oversized uploads/payloads) ---
    MAX_CONTENT_LENGTH = _env_int("MAX_CONTENT_LENGTH_MB", 10) * 1024 * 1024

    # --- dev server binding (run.py) — 0.0.0.0 to reach it from other
    # devices on the same network (e.g. other phones/tablets at the
    # clinic); 127.0.0.1 keeps it local-only to this device. ---
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = _env_int("PORT", 5000)

    # --- site identity (used for canonical URLs, Open Graph tags, sitemap) ---
    SITE_URL = os.environ.get("SITE_URL", "https://multihospitalmanagementsystem.pythonanywhere.com")

    # --- support contact — WhatsApp click-to-chat, no API/credentials
    # needed. Digits only, country code first, no "+" (that's the format
    # wa.me links expect). ---
    SUPPORT_WHATSAPP_NUMBER = os.environ.get("SUPPORT_WHATSAPP_NUMBER", "254700459966")

    # --- new-account defaults — see admin/routes.py:users_create() ---
    DEFAULT_TEMP_PASSWORD = os.environ.get("DEFAULT_TEMP_PASSWORD", "ChangeMe123!")

    # --- login brute-force protection ---
    LOGIN_MAX_ATTEMPTS = _env_int("LOGIN_MAX_ATTEMPTS", 5)
    LOGIN_LOCKOUT_MINUTES = _env_int("LOGIN_LOCKOUT_MINUTES", 15)

    # --- pharmacy ---
    # System-wide fallback only — each hospital can override this from the
    # Pricing & System Settings page (Hospital.low_stock_threshold).
    LOW_STOCK_THRESHOLD_DEFAULT = _env_int("LOW_STOCK_THRESHOLD_DEFAULT", 10)

    # --- IntaSend (M-Pesa STK push subscription payments) —
    # see app/subscription/intasend_client.py. Leave unset to keep the
    # local simulation fallback for testing the payment flow without
    # live credentials.
    INTASEND_PUBLISHABLE_KEY = os.environ.get("INTASEND_PUBLISHABLE_KEY", "")
    INTASEND_SECRET_KEY = os.environ.get("INTASEND_SECRET_KEY", "")
    INTASEND_TEST_MODE = _env_bool("INTASEND_TEST_MODE", True)
    INTASEND_WEBHOOK_CHALLENGE = os.environ.get("INTASEND_WEBHOOK_CHALLENGE", "")

    # ==================================================================
    # Kenya Digital Health Agency (DHA) compliance settings
    # Every value below is optional; the compliance dashboard
    # (/compliance) shows what is and isn't configured.
    # ==================================================================

    # --- field-level encryption at rest (app/security/crypto.py) ---
    # DATA_ENCRYPTION_KEYS: comma-separated "keyid:base64key" pairs, each key
    # 32 random bytes (AES-256-GCM), e.g. generate one with
    #   python -c "import os,base64;print('k1:'+base64.urlsafe_b64encode(os.urandom(32)).decode())"
    # The FIRST key listed is used for new writes; older keys stay listed so
    # existing data can still be read until re-encrypted (key rotation).
    # Leave unset only for local testing: a key is then derived from
    # SECRET_KEY (works, but rotating SECRET_KEY would make data unreadable).
    DATA_ENCRYPTION_KEYS = os.environ.get("DATA_ENCRYPTION_KEYS", "")
    BLIND_INDEX_KEY = os.environ.get("BLIND_INDEX_KEY", "")
    AUDIT_HMAC_KEY = os.environ.get("AUDIT_HMAC_KEY", "")

    # --- multi-factor authentication ---
    MFA_ISSUER = os.environ.get("MFA_ISSUER", "MediCore HMIS")
    # Roles that MUST enrol in MFA before using the system. Organizations can
    # widen this to "all staff" from the Security page.
    MFA_REQUIRED_ROLES = [r.strip() for r in os.environ.get(
        "MFA_REQUIRED_ROLES", "CEO,Admin,Hospital Manager,System Maintainer").split(",") if r.strip()]

    # --- audit / retention ---
    # How long audit and record-version history is kept. Confirm the figure
    # with your legal adviser; it is a policy value, not something the code
    # can decide for you. Nothing here ever deletes audit rows.
    AUDIT_RETENTION_YEARS = _env_int("AUDIT_RETENTION_YEARS", 10)

    # --- emergency ("break-glass") access ---
    EMERGENCY_ACCESS_MINUTES = _env_int("EMERGENCY_ACCESS_MINUTES", 60)

    # --- Kenya Health Information Exchange (HIE) ---
    # HIE_MODE: off | mock | live
    #   mock = messages are built, validated and stored but NOT sent (use for
    #          certification dry runs and demos without credentials)
    #   live = messages are POSTed to HIE_BASE_URL
    HIE_MODE = os.environ.get("HIE_MODE", "off").strip().lower()
    HIE_BASE_URL = os.environ.get("HIE_BASE_URL", "").rstrip("/")
    HIE_AUTH_TYPE = os.environ.get("HIE_AUTH_TYPE", "bearer").strip().lower()  # bearer | basic | oauth2
    HIE_API_TOKEN = os.environ.get("HIE_API_TOKEN", "")
    HIE_USERNAME = os.environ.get("HIE_USERNAME", "")
    HIE_PASSWORD = os.environ.get("HIE_PASSWORD", "")
    HIE_TOKEN_URL = os.environ.get("HIE_TOKEN_URL", "")
    HIE_CLIENT_ID = os.environ.get("HIE_CLIENT_ID", "")
    HIE_CLIENT_SECRET = os.environ.get("HIE_CLIENT_SECRET", "")
    HIE_TIMEOUT_SECONDS = _env_int("HIE_TIMEOUT_SECONDS", 15)
    # Endpoint paths are configurable because the exact API contract comes
    # from DHA's integration documentation during certification.
    HIE_BUNDLE_PATH = os.environ.get("HIE_BUNDLE_PATH", "/Bundle")
    HIE_PULL_PATH = os.environ.get("HIE_PULL_PATH", "/Patient?identifier={system}|{value}")
    HPT_SEARCH_PATH = os.environ.get("HPT_SEARCH_PATH", "/products?search={q}")
    # DHA identification_type spellings. Defaults follow hie-docs.dha.go.ke (Patient Search and
    # Eligibility Check guides use different spellings). Override with a JSON object if UAT differs.
    HIE_SEARCH_ID_TYPE_MAP = os.environ.get("HIE_SEARCH_ID_TYPE_MAP", "")
    HIE_ELIGIBILITY_ID_TYPE_MAP = os.environ.get("HIE_ELIGIBILITY_ID_TYPE_MAP", "")
    FORCE_HTTPS = _env_bool("FORCE_HTTPS", False)
    IDLE_LOGOUT_MINUTES = _env_int("IDLE_LOGOUT_MINUTES", 15)
    HIE_AUTO_SEND = _env_bool("HIE_AUTO_SEND", False)  # queue+send on consultation completion (needs 'hie' consent)
    HIE_INBOUND_SECRET = os.environ.get("HIE_INBOUND_SECRET", "")  # HMAC secret for /hie/inbound
    HIE_IDENTIFIER_BASE = os.environ.get("HIE_IDENTIFIER_BASE", "https://medicore.example/identifier").rstrip("/")

    # --- Health Products & Technologies (HPT) registry ---
    HPT_REGISTRY_URL = os.environ.get("HPT_REGISTRY_URL", "").rstrip("/")
    HPT_REGISTRY_TOKEN = os.environ.get("HPT_REGISTRY_TOKEN", "")

    # --- disease surveillance submission (KHIS / DHIS2 style endpoint) ---
    SURVEILLANCE_MODE = os.environ.get("SURVEILLANCE_MODE", "off").strip().lower()  # off | mock | live
    SURVEILLANCE_URL = os.environ.get("SURVEILLANCE_URL", "").rstrip("/")
    SURVEILLANCE_TOKEN = os.environ.get("SURVEILLANCE_TOKEN", "")
    SURVEILLANCE_PATH_NOTIFY = os.environ.get("SURVEILLANCE_PATH_NOTIFY", "/notifications")
    SURVEILLANCE_PATH_WEEKLY = os.environ.get("SURVEILLANCE_PATH_WEEKLY", "/dataValueSets")
    SURVEILLANCE_PATH_QUALITY = os.environ.get("SURVEILLANCE_PATH_QUALITY", "/dataValueSets")
    SURVEILLANCE_USERNAME = os.environ.get("SURVEILLANCE_USERNAME", "")
    SURVEILLANCE_PASSWORD = os.environ.get("SURVEILLANCE_PASSWORD", "")

    # --- Digital Health ID sign-in (OpenID Connect) ---
    DHA_OIDC_ISSUER = os.environ.get("DHA_OIDC_ISSUER", "").rstrip("/")
    DHA_OIDC_CLIENT_ID = os.environ.get("DHA_OIDC_CLIENT_ID", "")
    DHA_OIDC_CLIENT_SECRET = os.environ.get("DHA_OIDC_CLIENT_SECRET", "")
    DHA_OIDC_REDIRECT_URI = os.environ.get("DHA_OIDC_REDIRECT_URI", "")
    DHA_OIDC_SCOPES = os.environ.get("DHA_OIDC_SCOPES", "openid profile email")
    DHA_OIDC_TRUST_MFA = _env_bool("DHA_OIDC_TRUST_MFA", False)

    # --- backups & disaster recovery ---
    BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(BASE_DIR, "backups"))
    BACKUP_KEEP_DAYS = _env_int("BACKUP_KEEP_DAYS", 35)
    BACKUP_OFFSITE_DIR = os.environ.get("BACKUP_OFFSITE_DIR", "")  # a mounted/synced folder outside this server
    BACKUP_S3_BUCKET = os.environ.get("BACKUP_S3_BUCKET", "")       # needs `pip install boto3`
    BACKUP_S3_PREFIX = os.environ.get("BACKUP_S3_PREFIX", "medicore/")
    BACKUP_S3_ENDPOINT_URL = os.environ.get("BACKUP_S3_ENDPOINT_URL", "")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    """Picks a config class from APP_ENV (development/production/testing),
    defaulting to development. create_app() uses this when no explicit
    config_class is passed in."""
    env = os.environ.get("APP_ENV", "development").lower()
    config_class = CONFIG_MAP.get(env, DevelopmentConfig)
    if env == "production" and config_class.SECRET_KEY == "change-this-in-production-please":
        raise RuntimeError(
            "Set a real SECRET_KEY environment variable before running with APP_ENV=production — "
            "the default one is public (it's in this file) and anyone who has it can forge sessions."
        )
    return config_class
