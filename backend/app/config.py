import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(str(os.getenv(name) or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


@dataclass(frozen=True)
class Settings:
    access_key: str
    ops_key: str
    database_url: str
    poll_interval_seconds: int
    request_timeout_seconds: int
    metrics_retention_days: int
    event_retention_days: int
    cookie_secure: bool
    auto_migrate: bool
    webhook_urls: tuple[str, ...]
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_to: tuple[str, ...]
    smtp_starttls: bool
    taem_api_url: str
    taem_username: str
    taem_password: str
    taem_timeout_seconds: int

    @property
    def webhook_enabled(self) -> bool:
        return bool(self.webhook_urls)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.smtp_to)


def load_settings() -> Settings:
    data_dir = BASE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    webhook_urls = tuple(
        value.strip()
        for value in str(os.getenv("ALERT_WEBHOOK_URLS") or "").split(",")
        if value.strip()
    )
    smtp_to = tuple(
        value.strip()
        for value in str(os.getenv("ALERT_SMTP_TO") or "").split(",")
        if value.strip()
    )
    return Settings(
        access_key=str(os.getenv("MANAGER_ACCESS_KEY") or "").strip(),
        ops_key=str(os.getenv("ADOBE2API_OPS_KEY") or "").strip(),
        database_url=str(
            os.getenv("DATABASE_URL")
            or f"sqlite:///{(data_dir / 'manager.db').as_posix()}"
        ),
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 30, 5),
        request_timeout_seconds=_env_int("REMOTE_TIMEOUT_SECONDS", 8, 1),
        metrics_retention_days=_env_int("METRICS_RETENTION_DAYS", 7, 1),
        event_retention_days=_env_int("EVENT_RETENTION_DAYS", 90, 1),
        cookie_secure=_env_bool("MANAGER_COOKIE_SECURE", True),
        auto_migrate=_env_bool("MANAGER_AUTO_MIGRATE", True),
        webhook_urls=webhook_urls,
        smtp_host=str(os.getenv("ALERT_SMTP_HOST") or "").strip(),
        smtp_port=_env_int("ALERT_SMTP_PORT", 587, 1),
        smtp_username=str(os.getenv("ALERT_SMTP_USERNAME") or "").strip(),
        smtp_password=str(os.getenv("ALERT_SMTP_PASSWORD") or ""),
        smtp_from=str(os.getenv("ALERT_SMTP_FROM") or "").strip(),
        smtp_to=smtp_to,
        smtp_starttls=_env_bool("ALERT_SMTP_STARTTLS", True),
        taem_api_url=str(os.getenv("TAEM_API_URL") or "").strip().rstrip("/"),
        taem_username=str(os.getenv("TAEM_USERNAME") or "").strip(),
        taem_password=str(os.getenv("TAEM_PASSWORD") or ""),
        taem_timeout_seconds=_env_int("TAEM_TIMEOUT_SECONDS", 960, 30),
    )


settings = load_settings()
