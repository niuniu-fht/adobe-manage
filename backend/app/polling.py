import asyncio
import time
from contextlib import suppress
from typing import Any, Optional

from sqlalchemy import delete, select

from .alerts import evaluate_alerts, notification_payload
from .config import settings
from .database import SessionLocal
from .models import AlertEvent, AlertSilence, AuditEvent, Instance, MetricSample
from .notifications import notification_service
from .remote import RemoteError, remote_client


class FleetPoller:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None
        self._last_cleanup = 0.0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="fleet-poller")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._stop = None

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            return
        while not stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> None:
        with SessionLocal() as db:
            targets = [
                (item.id, item.base_url)
                for item in db.scalars(
                    select(Instance).where(Instance.enabled.is_(True))
                ).all()
            ]
        if targets:
            await asyncio.gather(
                *(self._poll_instance(instance_id, base_url) for instance_id, base_url in targets)
            )
        if time.time() - self._last_cleanup >= 3600:
            self.cleanup()

    async def _poll_instance(self, instance_id: str, base_url: str) -> None:
        started = time.perf_counter()
        snapshot: Optional[dict[str, Any]] = None
        error = ""
        try:
            snapshot = await remote_client.snapshot(base_url)
        except RemoteError as exc:
            error = str(exc)
        latency = time.perf_counter() - started
        now_ts = time.time()

        with SessionLocal() as db:
            instance = db.get(Instance, instance_id)
            if instance is None:
                return
            succeeded = snapshot is not None
            if succeeded:
                was_offline = instance.state == "offline"
                instance.consecutive_failures = 0
                instance.consecutive_successes += 1
                if not was_offline or instance.consecutive_successes >= 2:
                    instance.state = "online"
                instance.last_seen_at = now_ts
                instance.last_error = ""
                instance.last_latency_seconds = latency
                instance.last_snapshot = snapshot
                instance.ops_api_version = int(snapshot.get("ops_api_version") or 0)
                instance.capabilities = list(snapshot.get("capabilities") or [])
            else:
                instance.consecutive_successes = 0
                instance.consecutive_failures += 1
                instance.last_failure_at = now_ts
                instance.last_error = error[:1000]
                instance.last_latency_seconds = latency
                if instance.consecutive_failures >= 3:
                    instance.state = "offline"

            request_stats = snapshot.get("requests", {}) if snapshot else {}
            token_stats = snapshot.get("tokens", {}) if snapshot else {}
            request_total = int(request_stats.get("total") or 0)
            failed_requests = int(request_stats.get("failed") or 0)
            db.add(
                MetricSample(
                    instance_id=instance.id,
                    ts=now_ts,
                    online=succeeded,
                    latency_seconds=latency,
                    request_total=request_total,
                    successful_requests=int(
                        request_stats.get("successful")
                        if request_stats.get("successful") is not None
                        else max(0, request_total - failed_requests)
                    ),
                    failed_requests=failed_requests,
                    error_rate=float(request_stats.get("error_rate") or 0),
                    duration_p95_seconds=float(
                        request_stats.get("duration_p95_seconds") or 0
                    ),
                    active_tokens=int(token_stats.get("active") or 0),
                    total_tokens=int(token_stats.get("total") or 0),
                    credits_available=float(token_stats.get("credits_available") or 0),
                    credits_total=float(token_stats.get("credits_total") or 0),
                    in_progress=int(request_stats.get("in_progress") or 0),
                )
            )
            db.flush()
            notifications = evaluate_alerts(
                db,
                instance,
                snapshot,
                poll_succeeded=succeeded,
                now=now_ts,
            )

        for item in notifications:
            await notification_service.send(notification_payload(item))

    def cleanup(self) -> None:
        now_ts = time.time()
        metric_cutoff = now_ts - settings.metrics_retention_days * 86400
        event_cutoff = now_ts - settings.event_retention_days * 86400
        with SessionLocal() as db:
            db.execute(delete(MetricSample).where(MetricSample.ts < metric_cutoff))
            db.execute(
                delete(AlertEvent).where(
                    AlertEvent.state == "resolved", AlertEvent.resolved_at < event_cutoff
                )
            )
            db.execute(delete(AuditEvent).where(AuditEvent.ts < event_cutoff))
            db.execute(delete(AlertSilence).where(AlertSilence.ends_at < now_ts))
            db.commit()
        self._last_cleanup = now_ts


fleet_poller = FleetPoller()
