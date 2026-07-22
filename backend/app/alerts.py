import time
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AlertEvent, AlertRule, AlertSilence, Instance


DEFAULT_RULES = [
    {
        "id": "instance_offline",
        "name": "Instance offline",
        "severity": "critical",
        "threshold": 1,
        "minimum_requests": 0,
        "pending_samples": 1,
        "recovery_samples": 1,
    },
    {
        "id": "high_latency",
        "name": "High collection latency",
        "severity": "warning",
        "threshold": 3.0,
        "minimum_requests": 0,
        "pending_samples": 3,
        "recovery_samples": 2,
    },
    {
        "id": "high_error_rate",
        "name": "High request error rate",
        "severity": "critical",
        "threshold": 0.20,
        "minimum_requests": 10,
        "pending_samples": 3,
        "recovery_samples": 2,
    },
    {
        "id": "no_active_tokens",
        "name": "No active tokens",
        "severity": "critical",
        "threshold": 0,
        "minimum_requests": 0,
        "pending_samples": 1,
        "recovery_samples": 2,
    },
    {
        "id": "low_credits",
        "name": "Low available credits",
        "severity": "warning",
        "threshold": 0.20,
        "minimum_requests": 0,
        "pending_samples": 2,
        "recovery_samples": 2,
    },
    {
        "id": "token_expiring",
        "name": "Token expires within 24 hours",
        "severity": "warning",
        "threshold": 1,
        "minimum_requests": 0,
        "pending_samples": 1,
        "recovery_samples": 1,
    },
    {
        "id": "refresh_failures",
        "name": "Refresh profile repeatedly failing",
        "severity": "critical",
        "threshold": 3,
        "minimum_requests": 0,
        "pending_samples": 1,
        "recovery_samples": 2,
    },
]


@dataclass
class AlertNotification:
    transition: str
    event: AlertEvent
    rule: AlertRule
    instance: Instance


def seed_alert_rules(db: Session) -> None:
    for payload in DEFAULT_RULES:
        if db.get(AlertRule, payload["id"]) is None:
            db.add(AlertRule(**payload))
    db.commit()


def _condition(
    rule: AlertRule,
    instance: Instance,
    snapshot: Optional[dict[str, Any]],
    *,
    poll_succeeded: bool,
) -> Optional[tuple[bool, float, str]]:
    if rule.id == "instance_offline":
        value = 1.0 if instance.state == "offline" else 0.0
        return value >= 1, value, instance.last_error or "Instance is unreachable"
    if not poll_succeeded or not snapshot:
        return None

    requests = snapshot.get("requests") if isinstance(snapshot.get("requests"), dict) else {}
    tokens = snapshot.get("tokens") if isinstance(snapshot.get("tokens"), dict) else {}
    profiles = (
        snapshot.get("refresh_profiles")
        if isinstance(snapshot.get("refresh_profiles"), dict)
        else {}
    )
    if rule.id == "high_latency":
        value = float(instance.last_latency_seconds or 0)
        return value > rule.threshold, value, f"Collection latency is {value:.2f}s"
    if rule.id == "high_error_rate":
        total = int(requests.get("total") or 0)
        value = float(requests.get("error_rate") or 0)
        triggered = total >= rule.minimum_requests and value > rule.threshold
        return triggered, value, f"5-minute error rate is {value:.1%} across {total} requests"
    if rule.id == "no_active_tokens":
        value = float(tokens.get("active") or 0)
        return value <= rule.threshold, value, "No active token is available"
    if rule.id == "low_credits":
        total = float(tokens.get("credits_total") or 0)
        available = float(tokens.get("credits_available") or 0)
        value = available / total if total > 0 else 1.0
        return total > 0 and value < rule.threshold, value, f"Available credits are {value:.1%}"
    if rule.id == "token_expiring":
        value = float(tokens.get("expiring_24h") or 0)
        return value >= rule.threshold, value, f"{int(value)} token(s) expire within 24 hours"
    if rule.id == "refresh_failures":
        value = float(profiles.get("consecutive_failures_max") or 0)
        return value >= rule.threshold, value, f"Refresh failed {int(value)} consecutive times"
    return None


def _is_silenced(db: Session, instance_id: str, rule_id: str, now: float) -> bool:
    silences = db.scalars(
        select(AlertSilence).where(
            AlertSilence.starts_at <= now,
            AlertSilence.ends_at > now,
        )
    ).all()
    return any(
        (not item.instance_id or item.instance_id == instance_id)
        and (not item.rule_id or item.rule_id == rule_id)
        for item in silences
    )


def evaluate_alerts(
    db: Session,
    instance: Instance,
    snapshot: Optional[dict[str, Any]],
    *,
    poll_succeeded: bool,
    now: Optional[float] = None,
) -> list[AlertNotification]:
    now_ts = float(now if now is not None else time.time())
    notifications: list[AlertNotification] = []
    rules = db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))).all()
    for rule in rules:
        result = _condition(rule, instance, snapshot, poll_succeeded=poll_succeeded)
        if result is None:
            continue
        triggered, value, message = result
        event = db.scalar(
            select(AlertEvent)
            .where(
                AlertEvent.instance_id == instance.id,
                AlertEvent.rule_id == rule.id,
                AlertEvent.state.in_(["pending", "firing"]),
            )
            .order_by(AlertEvent.id.desc())
        )
        silenced = _is_silenced(db, instance.id, rule.id, now_ts)

        if triggered:
            if event is None:
                event = AlertEvent(
                    instance_id=instance.id,
                    rule_id=rule.id,
                    state="pending",
                    severity=rule.severity,
                    message=message,
                    value=value,
                    consecutive_hits=1,
                    opened_at=now_ts,
                    updated_at=now_ts,
                )
                db.add(event)
                db.flush()
            else:
                event.consecutive_hits += 1
                event.consecutive_recoveries = 0
                event.value = value
                event.message = message
                event.updated_at = now_ts

            if event.state == "pending" and event.consecutive_hits >= rule.pending_samples:
                event.state = "firing"
                event.firing_at = now_ts
                if not silenced:
                    event.last_notified_at = now_ts
                    notifications.append(AlertNotification("firing", event, rule, instance))
            elif (
                event.state == "firing"
                and not silenced
                and (event.last_notified_at or 0) <= now_ts - 7200
            ):
                event.last_notified_at = now_ts
                notifications.append(AlertNotification("reminder", event, rule, instance))
            continue

        if event is None:
            continue
        if event.state == "pending":
            event.state = "resolved"
            event.resolved_at = now_ts
            event.updated_at = now_ts
            continue
        event.consecutive_recoveries += 1
        event.updated_at = now_ts
        if event.consecutive_recoveries >= rule.recovery_samples:
            event.state = "resolved"
            event.resolved_at = now_ts
            if not silenced:
                event.last_notified_at = now_ts
                notifications.append(AlertNotification("resolved", event, rule, instance))

    db.commit()
    return notifications


def notification_payload(item: AlertNotification) -> dict[str, Any]:
    return {
        "event": "alert",
        "state": item.transition,
        "severity": item.rule.severity,
        "rule_id": item.rule.id,
        "rule_name": item.rule.name,
        "message": item.event.message,
        "value": item.event.value,
        "timestamp": time.time(),
        "instance": {
            "id": item.instance.id,
            "name": item.instance.name,
            "location": item.instance.location,
            "base_url": item.instance.base_url,
        },
    }
