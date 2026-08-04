from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    access_key: str = Field(min_length=1, max_length=500)


class InstanceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    location: str = Field(default="", max_length=160)
    base_url: str = Field(min_length=8, max_length=500)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.lower().startswith("https://"):
            raise ValueError("base_url must use https")
        return normalized

    @field_validator("name", "location")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(v.strip() for v in values if v.strip()))[:20]


class InstanceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    location: Optional[str] = Field(default=None, max_length=160)
    base_url: Optional[str] = Field(default=None, min_length=8, max_length=500)
    enabled: Optional[bool] = None
    tags: Optional[list[str]] = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized.lower().startswith("https://"):
            raise ValueError("base_url must use https")
        return normalized


class AlertRuleUpdate(BaseModel):
    enabled: Optional[bool] = None
    threshold: Optional[float] = None
    minimum_requests: Optional[int] = Field(default=None, ge=0)
    pending_samples: Optional[int] = Field(default=None, ge=1, le=20)
    recovery_samples: Optional[int] = Field(default=None, ge=1, le=20)


class SilenceRequest(BaseModel):
    duration_seconds: int = Field(ge=60, le=30 * 86400)
    rule_id: Optional[str] = None
    reason: str = Field(default="", max_length=300)


class ManagerPreferencesUpdate(BaseModel):
    low_credit_threshold: float = Field(ge=0, le=1_000_000_000)


class AutoReplacementSettingsUpdate(BaseModel):
    credit_threshold: float = Field(ge=0, le=1_000_000_000)
    refresh_interval_minutes: int = Field(ge=1, le=1440)
    enabled: bool = True


class AccountBatchRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


class AccountBatchEnabledRequest(AccountBatchRequest):
    enabled: bool


class AccountMoveRequest(AccountBatchRequest):
    target_instance_id: str = Field(min_length=1, max_length=100)


class AccountSafeReplaceRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("email must be a valid account email")
        return normalized


class FleetAccountTarget(BaseModel):
    instance_id: str = Field(min_length=1, max_length=100)
    target_count: int = Field(ge=0, le=1_000_000)


class FleetCookieImportItem(BaseModel):
    cookie: str = Field(min_length=1, max_length=200_000)
    name: Optional[str] = Field(default=None, max_length=300)


class FleetCookieImportRequest(BaseModel):
    items: list[FleetCookieImportItem] = Field(min_length=1, max_length=5000)
    targets: list[FleetAccountTarget] = Field(min_length=1, max_length=200)


class FleetLowCreditDeleteRequest(BaseModel):
    credit_threshold: float = Field(ge=0, le=1_000_000_000)


class JsonPayload(BaseModel):
    data: dict[str, Any]
