from typing import Any

from sqlalchemy.orm import Session

from .models import ManagerSetting


LOW_CREDIT_THRESHOLD_KEY = "low_credit_threshold"
DEFAULT_LOW_CREDIT_THRESHOLD = 100.0


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


def seed_manager_settings(db: Session) -> None:
    if db.get(ManagerSetting, LOW_CREDIT_THRESHOLD_KEY) is None:
        db.add(
            ManagerSetting(
                key=LOW_CREDIT_THRESHOLD_KEY,
                value=DEFAULT_LOW_CREDIT_THRESHOLD,
            )
        )
        db.commit()
