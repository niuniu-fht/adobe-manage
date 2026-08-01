import asyncio
import time
from contextlib import suppress
from typing import Any, Optional

from sqlalchemy import delete, select

from .alerts import evaluate_alerts, notification_payload
from .auto_replacements import auto_replacement_service
from .config import settings
from .database import SessionLocal
from .models import AlertEvent, AlertSilence, AuditEvent, Instance, MetricSample
from .notifications import notification_service
from .preferences import get_auto_replacement_settings, get_low_credit_threshold
from .remote import RemoteError, remote_client


class FleetPoller:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None
        self._last_cleanup = 0.0
        self._credit_refresh_running = False
        self._credit_refresh_started_at = 0.0
        self._credit_refresh_finished_at = 0.0
        self._credit_refresh_result: dict[str, Any] = {
            "instances": 0,
            "succeeded_instances": 0,
            "failed_instances": 0,
            "errors": [],
        }

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

    async def run_once(self, *, force_credit_refresh: bool = False) -> None:
        with SessionLocal() as db:
            low_credit_threshold = get_low_credit_threshold(db)
            auto_settings = get_auto_replacement_settings(db)
            targets = [
                (item.id, item.name, item.base_url)
                for item in db.scalars(
                    select(Instance).where(Instance.enabled.is_(True))
                ).all()
            ]
        if targets:
            await self._refresh_credits_if_due(
                targets,
                int(auto_settings["refresh_interval_minutes"]),
                force=force_credit_refresh,
            )
            await asyncio.gather(
                *(
                    self._poll_instance(
                        instance_id,
                        base_url,
                        low_credit_threshold,
                        float(auto_settings["credit_threshold"]),
                    )
                    for instance_id, _instance_name, base_url in targets
                )
            )
        if time.time() - self._last_cleanup >= 3600:
            self.cleanup()

    async def _poll_instance(
        self,
        instance_id: str,
        base_url: str,
        low_credit_threshold: float = 100.0,
        auto_replacement_credit_threshold: float = 0.0,
    ) -> None:
        started = time.perf_counter()
        snapshot: Optional[dict[str, Any]] = None
        auto_accounts: list[dict[str, Any]] = []
        error = ""
        try:
            snapshot = await remote_client.snapshot(base_url, low_credit_threshold)
            try:
                account_data = await remote_client.accounts(base_url, 0)
                auto_accounts = [
                    item
                    for item in (account_data.get("items") or [])
                    if isinstance(item, dict)
                ]
            except RemoteError:
                auto_accounts = []
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
            account_stats = snapshot.get("accounts", {}) if snapshot else {}
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
                    active_tokens=int(
                        account_stats.get("available")
                        if account_stats.get("available") is not None
                        else token_stats.get("active") or 0
                    ),
                    total_tokens=int(
                        account_stats.get("total")
                        if account_stats.get("total") is not None
                        else token_stats.get("total") or 0
                    ),
                    credits_available=float(
                        account_stats.get("credits_available")
                        if account_stats.get("credits_available") is not None
                        else token_stats.get("credits_available") or 0
                    ),
                    credits_total=float(
                        account_stats.get("credits_total")
                        if account_stats.get("credits_total") is not None
                        else token_stats.get("credits_total") or 0
                    ),
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
        if auto_accounts:
            await auto_replacement_service.observe_instance(
                instance_id=instance_id,
                instance_name=instance.name,
                base_url=base_url,
                accounts=auto_accounts,
                credit_threshold=auto_replacement_credit_threshold,
            )

    async def _refresh_credits_if_due(
        self,
        targets: list[tuple[str, str, str]],
        interval_minutes: int,
        *,
        force: bool,
    ) -> None:
        now = time.time()
        interval_seconds = max(1, int(interval_minutes)) * 60
        if self._credit_refresh_running:
            return
        if (
            not force
            and self._credit_refresh_finished_at
            and now < self._credit_refresh_finished_at + interval_seconds
        ):
            return

        self._credit_refresh_running = True
        self._credit_refresh_started_at = now

        async def refresh(instance_id: str, instance_name: str, base_url: str):
            try:
                await remote_client.request(
                    base_url,
                    "POST",
                    "/api/v1/tokens/credits/refresh-batch",
                    json={"ids": None},
                    timeout=180,
                )
                return {
                    "instance_id": instance_id,
                    "instance_name": instance_name,
                    "error": "",
                }
            except RemoteError as exc:
                return {
                    "instance_id": instance_id,
                    "instance_name": instance_name,
                    "error": str(exc)[:300],
                }

        try:
            results = await asyncio.gather(*(refresh(*target) for target in targets))
            errors = [item for item in results if item["error"]]
            self._credit_refresh_result = {
                "instances": len(results),
                "succeeded_instances": len(results) - len(errors),
                "failed_instances": len(errors),
                "errors": errors[:20],
            }
        finally:
            self._credit_refresh_finished_at = time.time()
            self._credit_refresh_running = False

    def credit_refresh_snapshot(self) -> dict[str, Any]:
        with SessionLocal() as db:
            interval_minutes = int(
                get_auto_replacement_settings(db)["refresh_interval_minutes"]
            )
        next_refresh_at = (
            self._credit_refresh_finished_at + interval_minutes * 60
            if self._credit_refresh_finished_at
            else time.time()
        )
        return {
            "running": self._credit_refresh_running,
            "started_at": self._credit_refresh_started_at or None,
            "finished_at": self._credit_refresh_finished_at or None,
            "next_refresh_at": next_refresh_at,
            **self._credit_refresh_result,
        }

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
