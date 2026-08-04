from typing import Any, Optional

import httpx

from .config import settings


class TaemError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class TaemClient:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._token = ""

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
        self._token = ""

    @staticmethod
    def _detail(data: Any, fallback: str) -> str:
        if isinstance(data, dict):
            detail = data.get("detail") or data.get("message")
            if isinstance(detail, dict):
                detail = detail.get("detail") or detail.get("message") or fallback
            if detail:
                return str(detail)[:500]
        if isinstance(data, str) and data.strip():
            return data.strip()[:500]
        return fallback

    async def _post(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        token: str = "",
        timeout: float,
    ) -> dict[str, Any]:
        url = f"{settings.taem_api_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = await self._get_client().post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise TaemError("母号服务处理超时", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise TaemError(f"母号服务连接失败：{exc}", status_code=502) from exc

        content_type = str(response.headers.get("content-type") or "")
        try:
            data = response.json() if "json" in content_type else response.text
        except Exception:
            data = response.text
        if response.status_code >= 400:
            raise TaemError(
                self._detail(data, f"母号服务返回 HTTP {response.status_code}"),
                status_code=response.status_code,
            )
        if not isinstance(data, dict):
            raise TaemError("母号服务返回了无效响应")
        return data

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        token: str = "",
        timeout: float,
    ) -> dict[str, Any]:
        url = f"{settings.taem_api_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = await self._get_client().get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise TaemError("母号服务处理超时", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise TaemError(f"母号服务连接失败：{exc}", status_code=502) from exc

        content_type = str(response.headers.get("content-type") or "")
        try:
            data = response.json() if "json" in content_type else response.text
        except Exception:
            data = response.text
        if response.status_code >= 400:
            raise TaemError(
                self._detail(data, f"母号服务返回 HTTP {response.status_code}"),
                status_code=response.status_code,
            )
        if not isinstance(data, dict):
            raise TaemError("母号服务返回了无效响应")
        return data

    def _ensure_configured(self) -> None:
        if not settings.taem_api_url:
            raise TaemError("TAEM_API_URL 未配置", status_code=503)
        if not settings.taem_username or not settings.taem_password:
            raise TaemError("TAEM_USERNAME 或 TAEM_PASSWORD 未配置", status_code=503)

    async def _login_token(self, *, force: bool = False) -> str:
        self._ensure_configured()
        if self._token and not force:
            return self._token
        login = await self._post(
            "/auth/login",
            payload={
                "username": settings.taem_username,
                "password": settings.taem_password,
            },
            timeout=30,
        )
        token_payload = login.get("token") if isinstance(login.get("token"), dict) else {}
        self._token = str(token_payload.get("access_token") or "").strip()
        if not self._token:
            raise TaemError("母号服务登录成功但未返回访问令牌", status_code=502)
        return self._token

    async def _authenticated_post(
        self, path: str, *, payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        for attempt in range(2):
            token = await self._login_token(force=attempt > 0)
            try:
                return await self._post(
                    path, payload=payload, token=token, timeout=timeout
                )
            except TaemError as exc:
                if exc.status_code != 401 or attempt:
                    raise
                self._token = ""
        raise TaemError("母号服务登录状态失效", status_code=401)

    async def _authenticated_get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        for attempt in range(2):
            token = await self._login_token(force=attempt > 0)
            try:
                return await self._get(
                    path, params=params, token=token, timeout=timeout
                )
            except TaemError as exc:
                if exc.status_code != 401 or attempt:
                    raise
                self._token = ""
        raise TaemError("母号服务登录状态失效", status_code=401)

    async def start_replace_member(self, email: str) -> dict[str, Any]:
        return await self._authenticated_post(
            "/adobe-accounts/replace-member",
            payload={"email": email},
            timeout=30,
        )

    async def start_replace_member_domain(
        self, email: str, *, source: str = "new_domain"
    ) -> dict[str, Any]:
        source = source if source in {"new_domain", "registered_reuse"} else "new_domain"
        return await self._authenticated_post(
            "/adobe-accounts/replace-member-domain",
            payload={
                "email": email,
                "domain": "code2alita.com",
                "prefix": "manager-auto",
                "auto": True,
                "source": source,
            },
            timeout=30,
        )

    async def start_remove_member_only(self, email: str) -> dict[str, Any]:
        return await self._authenticated_post(
            "/adobe-accounts/remove-member-only",
            payload={"email": email, "auto": True, "remove_only": True},
            timeout=30,
        )

    async def get_job(self, job_id: int, *, log_offset: int = 0) -> dict[str, Any]:
        return await self._authenticated_get(
            f"/adobe-accounts/jobs/{job_id}",
            params={"log_offset": max(0, log_offset)},
            timeout=30,
        )

    async def cancel_job(self, job_id: int) -> dict[str, Any]:
        return await self._authenticated_post(
            f"/adobe-accounts/jobs/{job_id}/cancel",
            payload={},
            timeout=30,
        )

    async def replace_member(self, email: str) -> dict[str, Any]:
        result = await self._authenticated_post(
            "/adobe-accounts/replace-member-cookie",
            payload={"email": email},
            timeout=settings.taem_timeout_seconds,
        )
        cookie = str(result.get("cookie") or "").strip()
        if not cookie:
            raise TaemError("母号补号流程结束，但未返回新 Cookie", status_code=502)
        return result


taem_client = TaemClient()
