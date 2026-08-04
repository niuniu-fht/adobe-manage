from typing import Any

from sqlalchemy.orm import Session

from .models import ManagerSetting


LOW_CREDIT_THRESHOLD_KEY = "low_credit_threshold"
DEFAULT_LOW_CREDIT_THRESHOLD = 100.0
ACCOUNT_TARGETS_KEY = "account_targets"
AUTO_REPLACEMENT_CREDIT_THRESHOLD_KEY = "auto_replacement_credit_threshold"
CREDITS_REFRESH_INTERVAL_MINUTES_KEY = "credits_refresh_interval_minutes"
AUTO_REPLACEMENT_ENABLED_KEY = "auto_replacement_enabled"
DEFAULT_AUTO_REPLACEMENT_CREDIT_THRESHOLD = 0.0
DEFAULT_CREDITS_REFRESH_INTERVAL_MINUTES = 5
DEFAULT_AUTO_REPLACEMENT_ENABLED = True


def normalize_low_credit_threshold(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_LOW_CREDIT_THRESHOLD
    return max(0.0, min(parsed, 1_000_000_000.0))


def get_low_credit_threshold(db: Session) -> float:
    row = db.get(ManagerSetting, LOW_CREDIT_THRESHOLD_KEY)
    return normalize_low_credit_threshold(
        row.value if row is not None else DEFAULT_LOW_CREDIT_THRESHOLD
    )


def set_low_credit_threshold(db: Session, value: Any) -> float:
    normalized = normalize_low_credit_threshold(value)
    row = db.get(ManagerSetting, LOW_CREDIT_THRESHOLD_KEY)
    if row is None:
        row = ManagerSetting(key=LOW_CREDIT_THRESHOLD_KEY, value=normalized)
        db.add(row)
    else:
        row.value = normalized
    db.commit()
    return normalized


def normalize_auto_replacement_credit_threshold(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_AUTO_REPLACEMENT_CREDIT_THRESHOLD
    return max(0.0, min(parsed, 1_000_000_000.0))


def normalize_credits_refresh_interval_minutes(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_CREDITS_REFRESH_INTERVAL_MINUTES
    return max(1, min(parsed, 1440))


def normalize_auto_replacement_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled", "开启"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled", "关闭"}:
            return False
    return DEFAULT_AUTO_REPLACEMENT_ENABLED


def get_auto_replacement_settings(db: Session) -> dict[str, float | int | bool]:
    threshold = db.get(ManagerSetting, AUTO_REPLACEMENT_CREDIT_THRESHOLD_KEY)
    interval = db.get(ManagerSetting, CREDITS_REFRESH_INTERVAL_MINUTES_KEY)
    enabled = db.get(ManagerSetting, AUTO_REPLACEMENT_ENABLED_KEY)
    return {
        "credit_threshold": normalize_auto_replacement_credit_threshold(
            threshold.value
            if threshold is not None
            else DEFAULT_AUTO_REPLACEMENT_CREDIT_THRESHOLD
        ),
        "refresh_interval_minutes": normalize_credits_refresh_interval_minutes(
            interval.value
            if interval is not None
            else DEFAULT_CREDITS_REFRESH_INTERVAL_MINUTES
        ),
        "enabled": normalize_auto_replacement_enabled(
            enabled.value if enabled is not None else DEFAULT_AUTO_REPLACEMENT_ENABLED
        ),
    }


def set_auto_replacement_settings(
    db: Session,
    *,
    credit_threshold: Any,
    refresh_interval_minutes: Any,
    enabled: Any = DEFAULT_AUTO_REPLACEMENT_ENABLED,
) -> dict[str, float | int | bool]:
    values = {
        AUTO_REPLACEMENT_CREDIT_THRESHOLD_KEY:
            normalize_auto_replacement_credit_threshold(credit_threshold),
        CREDITS_REFRESH_INTERVAL_MINUTES_KEY:
            normalize_credits_refresh_interval_minutes(refresh_interval_minutes),
        AUTO_REPLACEMENT_ENABLED_KEY: normalize_auto_replacement_enabled(enabled),
    }
    for key, value in values.items():
        row = db.get(ManagerSetting, key)
        if row is None:
            db.add(ManagerSetting(key=key, value=value))
        else:
            row.value = value
    db.commit()
    return {
        "credit_threshold": values[AUTO_REPLACEMENT_CREDIT_THRESHOLD_KEY],
        "refresh_interval_minutes": values[CREDITS_REFRESH_INTERVAL_MINUTES_KEY],
        "enabled": values[AUTO_REPLACEMENT_ENABLED_KEY],
    }


def normalize_account_targets(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        instance_id = str(raw_key or "").strip()
        if not instance_id:
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        result[instance_id] = max(0, min(count, 1_000_000))
    return result


def get_account_targets(db: Session) -> dict[str, int]:
    row = db.get(ManagerSetting, ACCOUNT_TARGETS_KEY)
    return normalize_account_targets(row.value if row is not None else {})


def set_account_targets(db: Session, value: Any) -> dict[str, int]:
    normalized = normalize_account_targets(value)
    row = db.get(ManagerSetting, ACCOUNT_TARGETS_KEY)
    if row is None:
        row = ManagerSetting(key=ACCOUNT_TARGETS_KEY, value=normalized)
        db.add(row)
    else:
        row.value = normalized
    db.commit()
    return normalized


def seed_manager_settings(db: Session) -> None:
    changed = False
    if db.get(ManagerSetting, LOW_CREDIT_THRESHOLD_KEY) is None:
        db.add(
            ManagerSetting(
                key=LOW_CREDIT_THRESHOLD_KEY,
                value=DEFAULT_LOW_CREDIT_THRESHOLD,
            )
        )
        changed = True
    if db.get(ManagerSetting, ACCOUNT_TARGETS_KEY) is None:
        db.add(ManagerSetting(key=ACCOUNT_TARGETS_KEY, value={}))
        changed = True
    if db.get(ManagerSetting, AUTO_REPLACEMENT_CREDIT_THRESHOLD_KEY) is None:
        db.add(
            ManagerSetting(
                key=AUTO_REPLACEMENT_CREDIT_THRESHOLD_KEY,
                value=DEFAULT_AUTO_REPLACEMENT_CREDIT_THRESHOLD,
            )
        )
        changed = True
    if db.get(ManagerSetting, CREDITS_REFRESH_INTERVAL_MINUTES_KEY) is None:
        db.add(
            ManagerSetting(
                key=CREDITS_REFRESH_INTERVAL_MINUTES_KEY,
                value=DEFAULT_CREDITS_REFRESH_INTERVAL_MINUTES,
            )
        )
        changed = True
    if db.get(ManagerSetting, AUTO_REPLACEMENT_ENABLED_KEY) is None:
        db.add(
            ManagerSetting(
                key=AUTO_REPLACEMENT_ENABLED_KEY,
                value=DEFAULT_AUTO_REPLACEMENT_ENABLED,
            )
        )
        changed = True
    if changed:
        db.commit()
