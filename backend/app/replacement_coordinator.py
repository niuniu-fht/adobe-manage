from __future__ import annotations

import threading


class ReplacementCoordinator:
    """One process-wide lease shared by manual and automatic replacements."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner = ""

    def try_acquire(self, owner: str) -> bool:
        owner = str(owner or "").strip()
        if not owner:
            return False
        with self._lock:
            if self._owner and self._owner != owner:
                return False
            self._owner = owner
            return True

    def release(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._owner = ""

    def current_owner(self) -> str:
        with self._lock:
            return self._owner

    def clear(self) -> None:
        with self._lock:
            self._owner = ""


replacement_coordinator = ReplacementCoordinator()
