from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .config import settings


class RemoteError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass
class RemoteResponse:
    status_code: int
    data: Any
    headers: dict[str, str]


class AdobeInstanceClient:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.request_timeout_seconds),
                follow_redirects=False,
            )
        return self._client

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Adobe2API-Ops-Key": settings.ops_key}

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Any = None,
        timeout: Optional[float] = None,
    ) -> RemoteResponse:
        if not settings.ops_key:
            raise RemoteError("ADOBE2API_OPS_KEY is not configured", status_code=503)
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = await self._get_client().request(
                method.upper(),
                url,
                params=params,
                json=json,
                headers=self.headers,
                timeout=timeout or settings.request_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise RemoteError("Instance request timed out", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise RemoteError(f"Instance connection failed: {exc}", status_code=502) from exc

        content_type = str(response.headers.get("content-type") or "")
        try:
            data = response.json() if "json" in content_type else response.text
        except Exception:
            data = response.text
        if response.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else str(data)[:300]
            raise RemoteError(
                f"Instance returned HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
                payload=data,
            )
        return RemoteResponse(response.status_code, data, dict(response.headers))

    async def snapshot(
        self, base_url: str, low_credit_threshold: float = 100.0
    ) -> dict[str, Any]:
        response = await self.request(
            base_url,
            "GET",
            "/api/v1/ops/snapshot",
            params={"low_credit_threshold": low_credit_threshold},
        )
        if not isinstance(response.data, dict):
            raise RemoteError("Invalid snapshot response")
        return response.data

    async def accounts(
        self, base_url: str, low_credit_threshold: float
    ) -> dict[str, Any]:
        response = await self.request(
            base_url,
            "GET",
            "/api/v1/ops/accounts",
            params={"low_credit_threshold": low_credit_threshold},
        )
        if not isinstance(response.data, dict):
            raise RemoteError("Invalid accounts response")
        return response.data

    async def logs(
        self,
        base_url: str,
        *,
        before_ts: Optional[float],
        limit: int,
        prompt: str,
        errors_only: bool,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": limit,
            "prompt": prompt,
            "errors_only": str(errors_only).lower(),
        }
        if before_ts is not None:
            params["before_ts"] = before_ts
        response = await self.request(
            base_url, "GET", "/api/v1/ops/logs", params=params
        )
        if not isinstance(response.data, dict):
            raise RemoteError("Invalid logs response")
        return response.data

    async def image_queue(
        self, base_url: str, *, limit: int = 200
    ) -> dict[str, Any]:
        response = await self.request(
            base_url,
            "GET",
            "/api/v1/image-queue",
            params={"limit": min(max(1, int(limit)), 1000)},
        )
        if not isinstance(response.data, dict):
            raise RemoteError("Invalid image queue response")
        return response.data


remote_client = AdobeInstanceClient()
