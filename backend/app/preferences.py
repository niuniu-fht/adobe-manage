from typing import Any

from sqlalchemy.orm import Session

from .models import ManagerSetting


LOW_CREDIT_THRESHOLD_KEY = "low_credit_threshold"
DEFAULT_LOW_CREDIT_THRESHOLD = 100.0
ACCOUNT_TARGETS_KEY = "account_targets"


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
    if changed:
        db.commit()
