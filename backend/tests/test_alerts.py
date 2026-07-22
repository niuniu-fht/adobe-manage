import asyncio
import time

from sqlalchemy import select

from app.alerts import evaluate_alerts, seed_alert_rules
from app.database import SessionLocal
from app.models import AlertEvent, AlertRule, Instance, MetricSample
from app.polling import fleet_poller
from app.remote import RemoteError, remote_client


def _snapshot(error_rate=0.0, total=20, active=2, credits_total=100, credits_available=100):
    return {
        "requests": {"error_rate": error_rate, "total": total},
        "tokens": {
            "active": active,
            "credits_total": credits_total,
            "credits_available": credits_available,
            "expiring_24h": 0,
        },
        "refresh_profiles": {"consecutive_failures_max": 0},
        "accounts": {
            "available": active,
            "low_credit": 0,
            "low_credit_threshold": 100,
        },
    }


def test_alert_pending_firing_and_recovery_transitions():
    with SessionLocal() as db:
        seed_alert_rules(db)
        instance = Instance(
            name="East",
            base_url="https://east.example",
            state="online",
            last_latency_seconds=4.0,
        )
        db.add(instance)
        db.commit()

        notifications = []
        for step in range(3):
            notifications.extend(
                evaluate_alerts(db, instance, _snapshot(), poll_succeeded=True, now=100 + step)
            )
        assert [item.transition for item in notifications] == ["firing"]

        instance.last_latency_seconds = 1.0
        notifications = []
        for step in range(2):
            notifications.extend(
                evaluate_alerts(db, instance, _snapshot(), poll_succeeded=True, now=200 + step)
            )
        assert [item.transition for item in notifications] == ["resolved"]
        event = db.scalar(
            select(AlertEvent).where(AlertEvent.rule_id == "high_latency")
        )
        assert event.state == "resolved"


def test_cleanup_removes_expired_metric_samples():
    with SessionLocal() as db:
        instance = Instance(name="East", base_url="https://east.example")
        db.add(instance)
        db.commit()
        db.add_all(
            [
                MetricSample(instance_id=instance.id, ts=time.time() - 8 * 86400),
                MetricSample(instance_id=instance.id, ts=time.time()),
            ]
        )
        db.commit()

    fleet_poller.cleanup()

    with SessionLocal() as db:
        rows = db.scalars(select(MetricSample)).all()
        assert len(rows) == 1


def test_three_instance_polling_marks_repeated_failure_offline(monkeypatch):
    with SessionLocal() as db:
        instances = [
            Instance(name="East", base_url="https://east.example"),
            Instance(name="West", base_url="https://west.example"),
            Instance(name="Down", base_url="https://down.example"),
        ]
        db.add_all(instances)
        db.commit()
        targets = [(item.id, item.base_url) for item in instances]

    async def fake_snapshot(base_url, _low_credit_threshold=100):
        if "down" in base_url:
            raise RemoteError("connection refused")
        return {
            "ops_api_version": 1,
            "capabilities": ["snapshot", "tokens"],
            "requests": {"total": 20, "error_rate": 0, "in_progress": 0},
            "tokens": {"total": 2, "active": 2, "credits_total": 100, "credits_available": 90},
            "refresh_profiles": {"consecutive_failures_max": 0},
            "accounts": {
                "total": 2,
                "available": 2,
                "low_credit": 0,
                "credits_total": 100,
                "credits_available": 90,
                "low_credit_threshold": 100,
            },
        }

    monkeypatch.setattr(remote_client, "snapshot", fake_snapshot)

    async def poll_all():
        for _ in range(3):
            for instance_id, base_url in targets:
                await fleet_poller._poll_instance(instance_id, base_url)

    asyncio.run(poll_all())

    with SessionLocal() as db:
        rows = {item.name: item for item in db.scalars(select(Instance)).all()}
        assert rows["East"].state == "online"
        assert rows["West"].state == "online"
        assert rows["Down"].state == "offline"
        assert rows["Down"].consecutive_failures == 3
    assert len(db.scalars(select(MetricSample)).all()) == 9


def test_low_credit_account_alert_fires_once_and_recovers_twice():
    with SessionLocal() as db:
        seed_alert_rules(db)
        instance = Instance(name="East", base_url="https://east.example", state="online")
        db.add(instance)
        db.commit()

        snapshot = _snapshot()
        snapshot["accounts"]["low_credit"] = 2
        notifications = evaluate_alerts(
            db, instance, snapshot, poll_succeeded=True, now=100
        )
        assert [item.transition for item in notifications if item.rule.id == "low_credits"] == [
            "firing"
        ]

        snapshot["accounts"]["low_credit"] = 0
        first_recovery = evaluate_alerts(
            db, instance, snapshot, poll_succeeded=True, now=200
        )
        second_recovery = evaluate_alerts(
            db, instance, snapshot, poll_succeeded=True, now=201
        )
        assert not [item for item in first_recovery if item.rule.id == "low_credits"]
        assert [
            item.transition for item in second_recovery if item.rule.id == "low_credits"
        ] == ["resolved"]
