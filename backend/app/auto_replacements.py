from __future__ import annotations

import asyncio
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import settings
from .database import SessionLocal
from .models import ManagerSetting
from .remote import RemoteError, remote_client
from .replacement_coordinator import replacement_coordinator
from .taem import TaemError, taem_client


AUTO_TERMINAL_STATUSES = {"done", "partial", "failed", "skipped"}


@dataclass
class AutoReplacementOperation:
    instance_id: str
    instance_name: str
    base_url: str
    profile_id: str
    source_email: str
    trigger: str
    credits_available: float | None
    health: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "queued"
    phase: str = "queued"
    upstream_job_id: int | None = None
    logs: list[str] = field(default_factory=list)
    error: str = ""
    replacement_email: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def latch_key(self) -> str:
        return f"{self.instance_id}:{self.profile_id}:{self.source_email}"

    def add_log(self, message: str) -> None:
        message = str(message or "").strip()
        if not message:
            return
        self.logs.append(f"{time.strftime('%H:%M:%S')} {message[:900]}")
        self.logs = self.logs[-500:]
        self.updated_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
            "profile_id": self.profile_id,
            "source_email": self.source_email,
            "trigger": self.trigger,
            "credits_available": self.credits_available,
            "health": self.health,
            "status": self.status,
            "phase": self.phase,
            "upstream_job_id": self.upstream_job_id,
            "logs": list(self.logs),
            "error": self.error,
            "replacement_email": self.replacement_email,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AutoReplacementService:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None
        self._queue: asyncio.Queue[AutoReplacementOperation] | None = None
        self._operations: dict[str, AutoReplacementOperation] = {}
        self._latched: set[str] = set()
        self._active_id = ""
        self._lock = threading.RLock()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._queue = asyncio.Queue()
            self._task = asyncio.create_task(
                self._run(), name="auto-domain-replacement-worker"
            )

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._stop = None
        self._queue = None

    async def observe_instance(
        self,
        *,
        instance_id: str,
        instance_name: str,
        base_url: str,
        accounts: list[dict[str, Any]],
    ) -> int:
        queued = 0
        zero_credit_guards = self._zero_credit_guards()
        guards_changed = False
        for item in accounts:
            if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                continue
            profile_id = str(item.get("id") or "").strip()
            email = self._account_email(item)
            health = str(item.get("health") or "").strip().lower()
            credits = self._optional_float(item.get("credits_available"))
            triggers = []
            if credits is not None and credits == 0:
                triggers.append("积分为 0")
            if health == "credential_error":
                triggers.append("凭证异常")
            if not profile_id or not email:
                continue

            if credits is not None and credits > 0 and email in zero_credit_guards:
                zero_credit_guards.pop(email, None)
                guards_changed = True
            if (
                credits == 0
                and email in zero_credit_guards
                and health != "credential_error"
            ):
                continue

            key = f"{instance_id}:{profile_id}:{email}"
            if not triggers:
                with self._lock:
                    self._latched.discard(key)
                continue
            with self._lock:
                if key in self._latched:
                    continue
                self._latched.add(key)
                operation = AutoReplacementOperation(
                    instance_id=instance_id,
                    instance_name=instance_name,
                    base_url=base_url,
                    profile_id=profile_id,
                    source_email=email,
                    trigger="、".join(triggers),
                    credits_available=credits,
                    health=health,
                )
                operation.add_log(
                    f"自动触发 [{instance_name}] {email}: {operation.trigger}"
                )
                operation.add_log("已进入串行队列，等待前序补号任务结束")
                self._operations[operation.id] = operation
                self._trim_locked()
            queue = self._queue
            if queue is None:
                raise RuntimeError("自动补号服务尚未启动")
            queue.put_nowait(operation)
            queued += 1
        if guards_changed:
            self._save_zero_credit_guards(zero_credit_guards)
        return queued

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            operations = sorted(
                self._operations.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )[:50]
            return {
                "active_id": self._active_id or None,
                "active": bool(self._active_id),
                "queued": self._queue.qsize() if self._queue is not None else 0,
                "operations": [item.snapshot() for item in operations],
            }

    def clear(self) -> None:
        with self._lock:
            self._operations.clear()
            self._latched.clear()
            self._active_id = ""
        queue = self._queue
        while queue is not None:
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def _run(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            operation = await queue.get()
            owner = f"auto:{operation.id}"
            try:
                wait_logged = False
                while not replacement_coordinator.try_acquire(owner):
                    if not wait_logged:
                        operation.add_log("另一个移除补号流程正在运行，继续排队")
                        wait_logged = True
                    await asyncio.sleep(2)
                with self._lock:
                    self._active_id = operation.id
                await self._process(operation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._fail(operation, f"自动补号任务异常:{exc}")
            finally:
                replacement_coordinator.release(owner)
                with self._lock:
                    if self._active_id == operation.id:
                        self._active_id = ""
                queue.task_done()

    async def _process(self, operation: AutoReplacementOperation) -> None:
        operation.status = "running"
        operation.phase = "checking"
        operation.add_log("开始复核实例账号状态")
        try:
            data = await remote_client.accounts(operation.base_url, 0)
        except RemoteError as exc:
            self._fail(operation, f"复核实例账号失败:{exc}")
            return
        current = next(
            (
                item
                for item in (data.get("items") or [])
                if isinstance(item, dict)
                and str(item.get("id") or "").strip() == operation.profile_id
            ),
            None,
        )
        if current is None:
            operation.status = "skipped"
            operation.phase = "complete"
            operation.add_log("实例中已找不到该账号，本次跳过")
            return
        current_health = str(current.get("health") or "").strip().lower()
        current_credits = self._optional_float(current.get("credits_available"))
        still_invalid = current_health == "credential_error" or current_credits == 0
        if not still_invalid:
            operation.status = "skipped"
            operation.phase = "complete"
            operation.add_log("账号状态已恢复，本次不执行移除")
            with self._lock:
                self._latched.discard(operation.latch_key)
            return

        operation.phase = "local_removal"
        operation.add_log("先从 Adobe 实例移除当前 Cookie 账号")
        try:
            response = await remote_client.request(
                operation.base_url,
                "POST",
                "/api/v1/refresh-profiles/delete-batch",
                json={"ids": [operation.profile_id]},
                timeout=180,
            )
        except RemoteError as exc:
            self._fail(operation, f"实例本地移除失败:{exc}")
            return
        delete_data = response.data if isinstance(response.data, dict) else {}
        if operation.profile_id not in self._confirmed_removed_ids(
            delete_data, [operation.profile_id]
        ):
            self._fail(operation, "实例未确认当前账号已移除，远端母号流程未启动")
            return
        operation.add_log("实例本地账号已移除，开始调用母号一次性域名补号")

        operation.phase = "mother_replacement"
        try:
            upstream = await taem_client.start_replace_member_domain(
                operation.source_email
            )
            operation.upstream_job_id = int(upstream.get("id"))
        except (TaemError, TypeError, ValueError) as exc:
            self._fail(operation, f"启动母号域名补号失败:{exc}")
            return
        operation.add_log(f"母号任务 #{operation.upstream_job_id} 已启动")
        offset = 0
        deadline = time.monotonic() + settings.taem_timeout_seconds
        upstream_result: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                upstream = await taem_client.get_job(
                    operation.upstream_job_id,
                    log_offset=offset,
                )
            except TaemError as exc:
                self._fail(operation, f"读取母号任务失败:{exc}")
                return
            logs = upstream.get("logs") if isinstance(upstream.get("logs"), list) else []
            for line in logs:
                operation.add_log(str(line))
            offset = max(offset + len(logs), int(upstream.get("log_total") or 0))
            upstream_status = str(upstream.get("status") or "running")
            if upstream_status == "running":
                await asyncio.sleep(2)
                continue
            if upstream_status != "done":
                detail = str(upstream.get("error") or f"任务状态:{upstream_status}")
                self._fail(operation, f"母号域名补号结束:{detail}")
                return
            upstream_result = (
                upstream.get("result")
                if isinstance(upstream.get("result"), dict)
                else {}
            )
            break
        else:
            self._fail(operation, "母号域名补号等待超时")
            return

        replacement = (
            upstream_result.get("replacement")
            if isinstance(upstream_result.get("replacement"), dict)
            else {}
        )
        cookie = str(replacement.get("cookie") or "").strip()
        replacement_email = str(replacement.get("email") or "").strip().lower()
        if not cookie:
            self._fail(operation, "母号任务已结束，但本次域名补号未返回 Cookie")
            return

        operation.phase = "importing"
        operation.replacement_email = replacement_email
        operation.add_log(f"域名子号 {replacement_email or '-'} 已生成，回写实例")
        try:
            response = await remote_client.request(
                operation.base_url,
                "POST",
                "/api/v1/refresh-profiles/import-cookie-batch",
                json={
                    "items": [
                        {
                            "cookie": {"cookie": cookie},
                            "name": replacement_email or operation.source_email,
                        }
                    ]
                },
                timeout=600,
            )
        except RemoteError as exc:
            self._fail(operation, f"新 Cookie 回写实例失败:{exc}")
            return
        import_data = response.data if isinstance(response.data, dict) else {}
        try:
            imported_count = int(import_data.get("imported_count") or 0)
            refresh_failed = int(import_data.get("refresh_failed_count") or 0)
        except (TypeError, ValueError):
            imported_count = 0
            refresh_failed = 0
        if imported_count < 1:
            failed = import_data.get("failed") if isinstance(import_data.get("failed"), list) else []
            detail = next(
                (
                    str(item.get("detail") or "")
                    for item in failed
                    if isinstance(item, dict) and item.get("detail")
                ),
                "实例未确认导入结果",
            )
            self._fail(operation, f"新 Cookie 回写实例失败:{detail}")
            return

        operation.phase = "complete"
        operation.status = "partial" if refresh_failed else "done"
        if replacement_email:
            self._guard_zero_credit(replacement_email)
            operation.add_log(
                "已启用新账号零积分保护；检测到正积分后自动解除"
            )
        if refresh_failed:
            operation.add_log("新 Cookie 已导入，但实例首次凭证刷新失败")
        else:
            operation.add_log("自动移除、一次性域名补号和 Cookie 回写全部完成")

    @staticmethod
    def _account_email(item: dict[str, Any]) -> str:
        email = str(item.get("email") or "").strip().lower()
        if "@" in email:
            return email
        name = str(item.get("name") or "").strip().lower()
        return name if "@" in name else ""

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _confirmed_removed_ids(
        data: dict[str, Any], attempted_ids: list[str]
    ) -> set[str]:
        attempted = set(attempted_ids)
        explicit = {
            str(value or "").strip()
            for key in ("deleted_ids", "missing_ids")
            for value in (data.get(key) or [])
            if str(value or "").strip() in attempted
        }
        if explicit:
            return explicit
        try:
            removed_count = int(data.get("deleted_count") or 0) + int(
                data.get("missing_count") or 0
            )
        except (TypeError, ValueError):
            removed_count = 0
        if removed_count >= len(attempted_ids) and str(data.get("status") or "") in {
            "ok",
            "partial",
        }:
            return attempted
        return set()

    @staticmethod
    def _fail(operation: AutoReplacementOperation, message: str) -> None:
        operation.status = "failed"
        operation.phase = "failed"
        operation.error = str(message or "")[:500]
        operation.add_log(operation.error)

    @staticmethod
    def _zero_credit_guards() -> dict[str, float]:
        with SessionLocal() as db:
            row = db.get(ManagerSetting, "auto_replacement_zero_credit_guards")
            value = row.value if row and isinstance(row.value, dict) else {}
        guards: dict[str, float] = {}
        for email, created_at in value.items():
            normalized = str(email or "").strip().lower()
            if "@" not in normalized:
                continue
            try:
                guards[normalized] = float(created_at or 0)
            except (TypeError, ValueError):
                guards[normalized] = 0.0
        return guards

    @staticmethod
    def _save_zero_credit_guards(guards: dict[str, float]) -> None:
        if len(guards) > 1000:
            guards = dict(
                sorted(guards.items(), key=lambda item: item[1], reverse=True)[:1000]
            )
        with SessionLocal() as db:
            row = db.get(ManagerSetting, "auto_replacement_zero_credit_guards")
            if row is None:
                row = ManagerSetting(
                    key="auto_replacement_zero_credit_guards",
                    value=dict(guards),
                )
                db.add(row)
            else:
                row.value = dict(guards)
                row.updated_at = time.time()
            db.commit()

    def _guard_zero_credit(self, email: str) -> None:
        normalized = str(email or "").strip().lower()
        if "@" not in normalized:
            return
        guards = self._zero_credit_guards()
        guards[normalized] = time.time()
        self._save_zero_credit_guards(guards)

    def _trim_locked(self) -> None:
        if len(self._operations) <= 100:
            return
        finished = sorted(
            (
                item
                for item in self._operations.values()
                if item.status in AUTO_TERMINAL_STATUSES
            ),
            key=lambda item: item.updated_at,
        )
        for item in finished[: len(self._operations) - 100]:
            self._operations.pop(item.id, None)


auto_replacement_service = AutoReplacementService()
