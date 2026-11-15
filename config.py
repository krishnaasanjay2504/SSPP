"""Central configuration for the Smart Student Placement Portal.

Every value can be overridden with an environment variable of the same name,
so the same code runs locally and on a hosted server.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-sspp-dev-secret-key")

    # ---- Oracle AI Database (connection "Oracle-local") ----
    DB_USER = os.environ.get("DB_USER", "SYSTEM")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "12345")
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", "1521"))
    DB_SERVICE = os.environ.get("DB_SERVICE", "FREEPDB1")
    DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "2"))
    DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))

    # ---- Files ----
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB uploads

    # ---- Notifications ----
    SMTP_HOST = os.environ.get("SMTP_HOST", "")          # empty = email disabled (logged only)
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_SENDER = os.environ.get("SMTP_SENDER", "placements@sspp.local")
    SMS_ENABLED = os.environ.get("SMS_ENABLED", "0") == "1"  # SMS gateway stub -> logs/sms_outbox.log
    REMINDER_INTERVAL_SEC = int(os.environ.get("REMINDER_INTERVAL_SEC", "300"))
    ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "1") == "1"

    # ---- Coding practice ----
    CODE_EXEC_ENABLED = os.environ.get("CODE_EXEC_ENABLED", "1") == "1"
    CODE_EXEC_TIMEOUT = int(os.environ.get("CODE_EXEC_TIMEOUT", "5"))

    # ---- AI assistant ----
    # "auto": use Claude when Anthropic credentials are present, otherwise the built-in rule engine.
    # "rules": never call an external model.
    AI_MODE = os.environ.get("AI_MODE", "auto")
    AI_MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")

    APP_NAME = "Smart Student Placement Portal"
    INSTITUTION = os.environ.get("INSTITUTION", "SSPP Institute of Technology")
