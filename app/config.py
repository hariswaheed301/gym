"""Environment-based configuration. Secrets come only from the environment / .env."""

import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class BaseConfig:
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://gym:gym@127.0.0.1:5432/gym"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.environ.get("SECRET_KEY")

    GYM_NAME = os.environ.get("GYM_NAME", "My Gym")
    GYM_TIMEZONE = os.environ.get("GYM_TIMEZONE", "Asia/Karachi")
    DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "PKR")
    DEVICE_CREDENTIAL_KEY = os.environ.get("DEVICE_CREDENTIAL_KEY", "")
    DEVICE_SYNC_MAX_RECORDS = int(os.environ.get("DEVICE_SYNC_MAX_RECORDS", "5000"))

    # allow | require_partial | require_full  (see README "Access policy")
    DUES_POLICY = os.environ.get("DUES_POLICY", "require_partial")
    EXPIRY_WARNING_DAYS = int(os.environ.get("EXPIRY_WARNING_DAYS", "7"))
    LONG_SESSION_HOURS = int(os.environ.get("LONG_SESSION_HOURS", "6"))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESSION_IDLE_MINUTES", "120")))
    SESSION_REFRESH_EACH_REQUEST = True  # sliding idle timeout

    WTF_CSRF_TIME_LIMIT = 8 * 3600
    FORCE_HTTPS = _bool("FORCE_HTTPS", False)
    TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_ENABLED = True
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024
    PER_PAGE = 20


class DevelopmentConfig(BaseConfig):
    DEBUG = False  # use `flask run --debug` explicitly


class ProductionConfig(BaseConfig):
    SESSION_COOKIE_SECURE = True
    FORCE_HTTPS = _bool("FORCE_HTTPS", True)


class TestingConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-production-0123456789"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://gym:gym@127.0.0.1:5432/gym_test"
    )
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    FORCE_HTTPS = False
    SESSION_COOKIE_SECURE = False
    DUES_POLICY = "require_partial"
    GYM_TIMEZONE = "Asia/Karachi"
    DEFAULT_CURRENCY = "PKR"
    LONG_SESSION_HOURS = 6


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
