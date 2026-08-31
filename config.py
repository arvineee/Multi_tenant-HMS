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
