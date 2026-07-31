from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


TERMINAL_STATUSES = {"done", "partial", "failed", "cancelled"}


@dataclass
class SafeReplacementOperation:
    instance_id: str
    instance_name: str
    profile_id: str
    source_email: str
    request_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "starting"
    phase: str = "starting"
    upstream_job_id: int | None = None
    target: int = 1
    success: int = 0
    fail: int = 0
    logs: list[str] = field(default_factory=list)
    upstream_log_offset: int = 0
    error: str = ""
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float = field(default_factory=time.perf_counter)
    cancel_requested: bool = False
    polling: bool = False
    lock_owner: str = ""

    def add_log(self, message: str) -> None:
        message = str(message or "").strip()
        if not message:
            return
        self.logs.append(message[:1000])
        self.logs = self.logs[-1000:]
        self.updated_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "phase": self.phase,
            "upstream_job_id": self.upstream_job_id,
            "target": self.target,
            "success": self.success,
            "fail": self.fail,
            "logs": list(self.logs),
            "error": self.error,
            "result": dict(self.result) if self.result else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "can_cancel": (
                self.status in {"starting", "running"}
                and self.phase in {"starting", "pulling"}
                and not self.cancel_requested
            ),
            "cancel_requested": self.cancel_requested,
        }


class SafeReplacementRegistry:
    def __init__(self) -> None:
        self._operations: dict[str, SafeReplacementOperation] = {}
        self._lock = threading.RLock()

    def create(
        self,
        *,
        instance_id: str,
        instance_name: str,
        profile_id: str,
        source_email: str,
        request_id: str,
    ) -> SafeReplacementOperation:
        with self._lock:
            active = self.find_active(instance_id, profile_id)
            if active:
                return active
            operation = SafeReplacementOperation(
                instance_id=instance_id,
                instance_name=instance_name,
                profile_id=profile_id,
                source_email=source_email,
                request_id=request_id,
            )
            self._operations[operation.id] = operation
            self._trim()
            return operation

    def get(self, operation_id: str) -> SafeReplacementOperation | None:
        with self._lock:
            return self._operations.get(operation_id)

    def find_active(
        self, instance_id: str, profile_id: str
    ) -> SafeReplacementOperation | None:
        with self._lock:
            for operation in self._operations.values():
                if (
                    operation.instance_id == instance_id
                    and operation.profile_id == profile_id
                    and operation.status not in TERMINAL_STATUSES
                ):
                    return operation
        return None

    def begin_poll(self, operation: SafeReplacementOperation) -> bool:
        with self._lock:
            if operation.polling:
                return False
            operation.polling = True
            return True

    def end_poll(self, operation: SafeReplacementOperation) -> None:
        with self._lock:
            operation.polling = False

    def clear(self) -> None:
        with self._lock:
            self._operations.clear()

    def _trim(self) -> None:
        if len(self._operations) <= 100:
            return
        finished = sorted(
            (
                operation
                for operation in self._operations.values()
                if operation.status in TERMINAL_STATUSES
            ),
            key=lambda operation: operation.updated_at,
        )
        for operation in finished[: len(self._operations) - 100]:
            self._operations.pop(operation.id, None)


safe_replacement_operations = SafeReplacementRegistry()
