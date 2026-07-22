import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .alerts import seed_alert_rules
from .config import settings
from .database import get_db
from .models import (
    AlertEvent,
    AlertRule,
    AlertSilence,
    AuditEvent,
    Instance,
    MetricSample,
)
from .notifications import notification_service
from .polling import fleet_poller
from .preferences import get_low_credit_threshold, set_low_credit_threshold
from .remote import RemoteError, remote_client
from .schemas import (
    AccountBatchEnabledRequest,
    AccountBatchRequest,
    AlertRuleUpdate,
    InstanceCreate,
    InstanceUpdate,
    LoginRequest,
    ManagerPreferencesUpdate,
    SilenceRequest,
)
from .security import (
    check_login_rate_limit,
    create_session,
    require_auth,
    require_csrf,
    verify_access_key,
)


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
api_router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or "")


def _instance_payload(item: Instance) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "location": item.location,
        "base_url": item.base_url,
        "enabled": item.enabled,
        "tags": item.tags or [],
        "state": item.state,
        "consecutive_failures": item.consecutive_failures,
        "last_seen_at": item.last_seen_at,
        "last_failure_at": item.last_failure_at,
        "last_error": item.last_error,
        "latency_seconds": item.last_latency_seconds,
        "ops_api_version": item.ops_api_version,
        "capabilities": item.capabilities or [],
        "snapshot": item.last_snapshot,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _get_instance(db: Session, instance_id: str) -> Instance:
    item = db.get(Instance, instance_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return item


def _ensure_compatible(item: Instance, capability: Optional[str] = None) -> None:
    if item.ops_api_version is not None and item.ops_api_version != 1:
        raise HTTPException(
            status_code=409,
            detail=f"Unsupported Ops API version: {item.ops_api_version}",
        )
    if capability and item.capabilities and capability not in item.capabilities:
        raise HTTPException(
            status_code=409,
            detail=f"Instance does not support capability: {capability}",
        )


def _record_audit(
    db: Session,
    *,
    request: Request,
    instance_id: Optional[str],
    action: str,
    outcome: str,
    started: float,
    resource_type: str = "",
    resource_id: str = "",
    detail: Optional[dict[str, Any]] = None,
) -> None:
    db.add(
        AuditEvent(
            instance_id=instance_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            duration_seconds=round(time.perf_counter() - started, 4),
            request_id=_request_id(request),
            detail=detail or {},
        )
    )
    db.commit()


async def _remote_action(
    db: Session,
    request: Request,
    instance: Instance,
    *,
    action: str,
    method: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
    body: Any = None,
    resource_type: str = "",
    resource_id: str = "",
    audit_detail: Optional[dict[str, Any]] = None,
    timeout: Optional[float] = None,
) -> Any:
    capability_by_resource = {
        "token": "tokens",
        "refresh_profile": "refresh_profiles",
        "config": "config",
        "logs": "cursor_logs",
    }
    _ensure_compatible(instance, capability_by_resource.get(resource_type))
    started = time.perf_counter()
    try:
        response = await remote_client.request(
            instance.base_url,
            method,
            path,
            params=params,
            json=body,
            timeout=timeout,
        )
    except RemoteError as exc:
        _record_audit(
            db,
            request=request,
            instance_id=instance.id,
            action=action,
            outcome="failed",
            started=started,
            resource_type=resource_type,
            resource_id=resource_id,
            detail={**(audit_detail or {}), "error": str(exc)[:300]},
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    _record_audit(
        db,
        request=request,
        instance_id=instance.id,
        action=action,
        outcome="success",
        started=started,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=audit_detail,
    )
    return response.data


def _decode_cursor(raw: str) -> dict[str, float]:
    if not raw:
        return {}
    try:
        padded = raw + "=" * ((4 - len(raw) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return {str(k): float(v) for k, v in data.items()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid log cursor") from exc


def _encode_cursor(data: dict[str, float]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@auth_router.post("/login")
def login(payload: LoginRequest, request: Request):
    client_ip = str(request.client.host if request.client else "unknown")
    check_login_rate_limit(client_ip)
    if not verify_access_key(payload.access_key):
        raise HTTPException(status_code=401, detail="Invalid access key")
    csrf_token = create_session(request)
    return {"status": "ok", "csrf_token": csrf_token, "expires_in": 43200}


@auth_router.get("/me")
def me(request: Request):
    try:
        require_auth(request)
    except HTTPException:
        return {"authenticated": False, "csrf_token": ""}
    return {
        "authenticated": True,
        "csrf_token": str((request.session or {}).get("csrf_token") or ""),
    }


@auth_router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@api_router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    instances = db.scalars(select(Instance).order_by(Instance.name)).all()
    active_alerts = db.scalars(
        select(AlertEvent).where(AlertEvent.state.in_(["pending", "firing"]))
    ).all()
    start_hour = int(time.time() // 3600) * 3600 - 167 * 3600
    samples = db.scalars(
        select(MetricSample).where(MetricSample.ts >= start_hour).order_by(MetricSample.ts)
    ).all()
    buckets: dict[str, dict[int, list[bool]]] = {}
    for sample in samples:
        hour = int(sample.ts // 3600) * 3600
        buckets.setdefault(sample.instance_id, {}).setdefault(hour, []).append(sample.online)
    payload = []
    for instance in instances:
        heartbeat = []
        instance_buckets = buckets.get(instance.id, {})
        for index in range(168):
            hour = start_hour + index * 3600
            values = instance_buckets.get(hour, [])
            heartbeat.append(
                {
                    "ts": hour,
                    "availability": round(sum(1 for value in values if value) / len(values), 3)
                    if values
                    else None,
                }
            )
        row = _instance_payload(instance)
        row["heartbeat"] = heartbeat
        row["active_alerts"] = sum(
            1 for alert in active_alerts if alert.instance_id == instance.id
        )
        payload.append(row)
    return {
        "instances": payload,
        "summary": {
            "total": len(instances),
            "online": sum(1 for item in instances if item.state == "online"),
            "offline": sum(1 for item in instances if item.state == "offline"),
            "active_alerts": len(active_alerts),
        },
        "preferences": {
            "low_credit_threshold": get_low_credit_threshold(db),
        },
        "updated_at": time.time(),
    }


@api_router.get("/instances")
def list_instances(db: Session = Depends(get_db)):
    return {
        "instances": [
            _instance_payload(item)
            for item in db.scalars(select(Instance).order_by(Instance.name)).all()
        ]
    }


@api_router.post("/instances", dependencies=[Depends(require_csrf)])
def create_instance(payload: InstanceCreate, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    item = Instance(**payload.model_dump())
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Instance name or URL already exists") from exc
    db.refresh(item)
    _record_audit(
        db,
        request=request,
        instance_id=item.id,
        action="instance.create",
        outcome="success",
        started=started,
        resource_type="instance",
        resource_id=item.id,
        detail={"name": item.name, "base_url": item.base_url},
    )
    return _instance_payload(item)


@api_router.get("/instances/{instance_id}")
def get_instance(instance_id: str, db: Session = Depends(get_db)):
    return _instance_payload(_get_instance(db, instance_id))


@api_router.put("/instances/{instance_id}", dependencies=[Depends(require_csrf)])
def update_instance(
    instance_id: str,
    payload: InstanceUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    item = _get_instance(db, instance_id)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(item, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Instance name or URL already exists") from exc
    _record_audit(
        db,
        request=request,
        instance_id=item.id,
        action="instance.update",
        outcome="success",
        started=started,
        resource_type="instance",
        resource_id=item.id,
        detail={"fields": sorted(changes)},
    )
    return _instance_payload(item)


@api_router.delete("/instances/{instance_id}", dependencies=[Depends(require_csrf)])
def delete_instance(instance_id: str, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    item = _get_instance(db, instance_id)
    item_name = item.name
    db.delete(item)
    db.commit()
    _record_audit(
        db,
        request=request,
        instance_id=None,
        action="instance.delete",
        outcome="success",
        started=started,
        resource_type="instance",
        resource_id=instance_id,
        detail={"name": item_name},
    )
    return {"status": "ok"}


@api_router.post("/instances/{instance_id}/test", dependencies=[Depends(require_csrf)])
async def test_instance(instance_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    started = time.perf_counter()
    try:
        snapshot = await remote_client.snapshot(item.base_url)
    except RemoteError as exc:
        _record_audit(
            db,
            request=request,
            instance_id=item.id,
            action="instance.test",
            outcome="failed",
            started=started,
            resource_type="instance",
            resource_id=item.id,
            detail={"error": str(exc)[:300]},
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    _record_audit(
        db,
        request=request,
        instance_id=item.id,
        action="instance.test",
        outcome="success",
        started=started,
        resource_type="instance",
        resource_id=item.id,
        detail={"ops_api_version": snapshot.get("ops_api_version")},
    )
    return snapshot


@api_router.get("/instances/{instance_id}/metrics")
def instance_metrics(
    instance_id: str,
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
):
    _get_instance(db, instance_id)
    rows = db.scalars(
        select(MetricSample)
        .where(
            MetricSample.instance_id == instance_id,
            MetricSample.ts >= time.time() - hours * 3600,
        )
        .order_by(MetricSample.ts)
    ).all()
    return {
        "items": [
            {
                "ts": row.ts,
                "online": row.online,
                "latency_seconds": row.latency_seconds,
                "request_total": row.request_total,
                "successful_requests": row.successful_requests,
                "failed_requests": row.failed_requests,
                "error_rate": row.error_rate,
                "duration_p95_seconds": row.duration_p95_seconds,
                "active_tokens": row.active_tokens,
                "credits_available": row.credits_available,
                "in_progress": row.in_progress,
            }
            for row in rows
        ]
    }


def _selected_instances(db: Session, instance_ids: Optional[str]) -> list[Instance]:
    query = select(Instance).where(Instance.enabled.is_(True)).order_by(Instance.name)
    if instance_ids:
        ids = [value.strip() for value in instance_ids.split(",") if value.strip()]
        query = query.where(Instance.id.in_(ids))
    return list(db.scalars(query).all())


ACCOUNT_FIELDS = (
    "id",
    "name",
    "display_name",
    "email",
    "user_id",
    "enabled",
    "health",
    "low_credit",
    "credits_available",
    "credits_total",
    "credits_updated_at",
    "credential_status",
    "credential_expires_at",
    "consecutive_failures",
    "last_attempt_at",
    "last_success_at",
    "next_refresh_at",
    "last_error",
    "imported_at",
)
ACCOUNT_SUMMARY_FIELDS = (
    "total",
    "available",
    "low_credit",
    "balance_unknown",
    "refresh_failing",
    "credential_error",
    "credits_available",
    "credits_total",
    "low_credit_threshold",
)
ACCOUNT_HEALTH_VALUES = {
    "healthy",
    "low_credit",
    "balance_unknown",
    "refresh_failed",
    "credential_error",
    "disabled",
}


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_account(item: Any, instance: Instance) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    profile_id = str(item.get("id") or "").strip()
    if not profile_id:
        return None
    payload = {field: item.get(field) for field in ACCOUNT_FIELDS}
    payload["id"] = profile_id
    payload["name"] = str(payload.get("name") or profile_id)[:200]
    payload["display_name"] = str(payload.get("display_name") or "")[:300]
    payload["email"] = str(payload.get("email") or "")[:320]
    payload["user_id"] = str(payload.get("user_id") or "")[:300]
    payload["last_error"] = str(payload.get("last_error") or "")[:1000]
    payload["enabled"] = bool(payload.get("enabled"))
    payload["low_credit"] = bool(payload.get("low_credit"))
    health = str(payload.get("health") or "balance_unknown")
    payload["health"] = health if health in ACCOUNT_HEALTH_VALUES else "balance_unknown"
    payload["credential_status"] = str(payload.get("credential_status") or "unknown")[:40]
    for field in (
        "credits_available",
        "credits_total",
        "credits_updated_at",
        "credential_expires_at",
        "last_attempt_at",
        "last_success_at",
        "next_refresh_at",
        "imported_at",
    ):
        payload[field] = _optional_float(payload.get(field))
    try:
        payload["consecutive_failures"] = max(
            0, int(payload.get("consecutive_failures") or 0)
        )
    except (TypeError, ValueError):
        payload["consecutive_failures"] = 0
    payload["instance_id"] = instance.id
    payload["instance_name"] = instance.name
    payload["duplicate"] = False
    return payload


def _sort_accounts(items: list[dict[str, Any]]) -> None:
    items.sort(
        key=lambda item: (
            item.get("credits_available") is None,
            float(item.get("credits_available") or 0),
            str(item.get("instance_name") or "").lower(),
            str(item.get("name") or "").lower(),
        )
    )


def _mark_duplicate_accounts(items: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        user_id = str(item.get("user_id") or "").strip().lower()
        email = str(item.get("email") or "").strip().lower()
        identity = f"user:{user_id}" if user_id else (f"email:{email}" if email else "")
        if identity:
            groups.setdefault(identity, []).append(item)
    for matches in groups.values():
        instance_names = sorted({str(item["instance_name"]) for item in matches})
        if len(instance_names) < 2:
            continue
        for item in matches:
            item["duplicate"] = True
            item["duplicate_instances"] = instance_names


async def _fetch_instance_accounts(
    item: Instance, low_credit_threshold: float
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _ensure_compatible(item, "accounts")
    data = await remote_client.accounts(item.base_url, low_credit_threshold)
    rows = []
    for remote_item in data.get("items", []):
        safe_item = _safe_account(remote_item, item)
        if safe_item is not None:
            rows.append(safe_item)
    remote_summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    summary = {field: remote_summary.get(field) for field in ACCOUNT_SUMMARY_FIELDS}
    return rows, summary


@api_router.get("/accounts")
async def aggregate_accounts(
    instance_ids: Optional[str] = None, db: Session = Depends(get_db)
):
    instances = _selected_instances(db, instance_ids)
    threshold = get_low_credit_threshold(db)

    async def fetch(item: Instance):
        try:
            rows, summary = await _fetch_instance_accounts(item, threshold)
            return item, rows, summary, None
        except (RemoteError, HTTPException) as exc:
            return item, [], {}, str(exc)

    results = await asyncio.gather(*(fetch(item) for item in instances))
    accounts: list[dict[str, Any]] = []
    errors = []
    summaries = {}
    for instance, rows, summary, error in results:
        if error:
            errors.append(
                {
                    "instance_id": instance.id,
                    "instance_name": instance.name,
                    "detail": error,
                }
            )
            continue
        accounts.extend(rows)
        summaries[instance.id] = summary
    _mark_duplicate_accounts(accounts)
    _sort_accounts(accounts)
    return {
        "status": "partial" if errors else "ok",
        "low_credit_threshold": threshold,
        "accounts": accounts,
        "instance_summaries": summaries,
        "errors": errors,
    }


@api_router.get("/instances/{instance_id}/accounts")
async def instance_accounts(instance_id: str, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    threshold = get_low_credit_threshold(db)
    try:
        accounts, summary = await _fetch_instance_accounts(item, threshold)
    except RemoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    _sort_accounts(accounts)
    return {
        "status": "ok",
        "low_credit_threshold": threshold,
        "accounts": accounts,
        "summary": summary,
    }


@api_router.get("/tokens")
async def aggregate_tokens(instance_ids: Optional[str] = None, db: Session = Depends(get_db)):
    instances = _selected_instances(db, instance_ids)

    async def fetch(item: Instance):
        try:
            _ensure_compatible(item, "tokens")
            response = await remote_client.request(item.base_url, "GET", "/api/v1/tokens")
            return item, response.data, None
        except (RemoteError, HTTPException) as exc:
            return item, None, str(exc)

    results = await asyncio.gather(*(fetch(item) for item in instances))
    tokens: list[dict[str, Any]] = []
    errors = []
    for instance, data, error in results:
        if error:
            errors.append({"instance_id": instance.id, "instance_name": instance.name, "detail": error})
            continue
        for token in (data or {}).get("tokens", []):
            tokens.append({**token, "instance_id": instance.id, "instance_name": instance.name})
    return {"status": "partial" if errors else "ok", "tokens": tokens, "errors": errors}


@api_router.get("/instances/{instance_id}/tokens")
async def instance_tokens(instance_id: str, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    _ensure_compatible(item, "tokens")
    try:
        return (await remote_client.request(item.base_url, "GET", "/api/v1/tokens")).data
    except RemoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.post("/instances/{instance_id}/tokens", dependencies=[Depends(require_csrf)])
async def add_tokens(instance_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    is_batch = isinstance(body.get("tokens"), list)
    path = "/api/v1/tokens/batch" if is_batch else "/api/v1/tokens"
    count = len(body.get("tokens") or []) if is_batch else 1
    return await _remote_action(
        db, request, item, action="token.add", method="POST", path=path, body=body,
        resource_type="token", audit_detail={"count": count},
    )


@api_router.delete("/instances/{instance_id}/tokens/{token_id}", dependencies=[Depends(require_csrf)])
async def delete_token(instance_id: str, token_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="token.delete", method="DELETE",
        path=f"/api/v1/tokens/{token_id}", resource_type="token", resource_id=token_id,
    )


@api_router.post("/instances/{instance_id}/tokens/delete-batch", dependencies=[Depends(require_csrf)])
async def delete_tokens_batch(instance_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    ids = body.get("ids") if isinstance(body.get("ids"), list) else []
    return await _remote_action(
        db, request, item, action="token.delete_batch", method="POST",
        path="/api/v1/tokens/delete-batch", body={"ids": ids}, resource_type="token",
        audit_detail={"count": len(ids)},
    )


@api_router.put("/instances/{instance_id}/tokens/{token_id}/status", dependencies=[Depends(require_csrf)])
async def set_token_status(
    instance_id: str, token_id: str, status: str, request: Request, db: Session = Depends(get_db)
):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="token.status", method="PUT",
        path=f"/api/v1/tokens/{token_id}/status", params={"status": status},
        resource_type="token", resource_id=token_id, audit_detail={"status": status},
    )


@api_router.put("/instances/{instance_id}/tokens/{token_id}/auto-refresh", dependencies=[Depends(require_csrf)])
async def set_token_auto_refresh(
    instance_id: str, token_id: str, enabled: bool, request: Request, db: Session = Depends(get_db)
):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="token.auto_refresh", method="PUT",
        path=f"/api/v1/tokens/{token_id}/auto-refresh", params={"enabled": str(enabled).lower()},
        resource_type="token", resource_id=token_id, audit_detail={"enabled": enabled},
    )


@api_router.post("/instances/{instance_id}/tokens/{token_id}/refresh", dependencies=[Depends(require_csrf)])
async def refresh_token(instance_id: str, token_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="token.refresh", method="POST",
        path=f"/api/v1/tokens/{token_id}/refresh", resource_type="token", resource_id=token_id,
        timeout=60,
    )


@api_router.post("/instances/{instance_id}/tokens/{token_id}/credits", dependencies=[Depends(require_csrf)])
async def refresh_token_credits(instance_id: str, token_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="token.credits", method="POST",
        path=f"/api/v1/tokens/{token_id}/credits/refresh", resource_type="token", resource_id=token_id,
        timeout=60,
    )


@api_router.post("/instances/{instance_id}/tokens/credits-batch", dependencies=[Depends(require_csrf)])
async def refresh_credits_batch(instance_id: str, request: Request, body: dict = Body(default={}), db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    ids = body.get("ids") if isinstance(body.get("ids"), list) else None
    return await _remote_action(
        db, request, item, action="token.credits_batch", method="POST",
        path="/api/v1/tokens/credits/refresh-batch", body={"ids": ids}, resource_type="token",
        audit_detail={"count": len(ids) if ids else 0}, timeout=120,
    )


@api_router.post("/instances/{instance_id}/tokens/export", dependencies=[Depends(require_csrf)])
async def export_tokens(instance_id: str, request: Request, body: dict = Body(default={}), db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    data = await _remote_action(
        db, request, item, action="token.export", method="POST", path="/api/v1/tokens/export",
        body={"ids": body.get("ids")}, resource_type="token",
        audit_detail={"count": len(body.get("ids") or [])},
    )
    content = json.dumps(data, ensure_ascii=False, indent=2).encode()
    return StreamingResponse(
        iter([content]), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{item.name}-tokens.json"'},
    )


@api_router.get("/instances/{instance_id}/refresh-profiles")
async def refresh_profiles(instance_id: str, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    _ensure_compatible(item, "refresh_profiles")
    try:
        return (await remote_client.request(item.base_url, "GET", "/api/v1/refresh-profiles")).data
    except RemoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.post("/instances/{instance_id}/refresh-profiles/import", dependencies=[Depends(require_csrf)])
async def import_refresh_profile(instance_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    is_batch = isinstance(body.get("items"), list)
    path = "/api/v1/refresh-profiles/import-cookie-batch" if is_batch else "/api/v1/refresh-profiles/import-cookie"
    count = len(body.get("items") or []) if is_batch else 1
    return await _remote_action(
        db, request, item, action="refresh_profile.import", method="POST", path=path,
        body=body, resource_type="refresh_profile", audit_detail={"count": count}, timeout=120,
    )


@api_router.post(
    "/instances/{instance_id}/refresh-profiles/delete-batch",
    dependencies=[Depends(require_csrf)],
)
async def delete_refresh_profiles_batch(
    instance_id: str,
    payload: AccountBatchRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    item = _get_instance(db, instance_id)
    ids = list(dict.fromkeys(value.strip() for value in payload.ids if value.strip()))
    if not ids:
        raise HTTPException(status_code=400, detail="ids is required")
    return await _remote_action(
        db,
        request,
        item,
        action="refresh_profile.delete_batch",
        method="POST",
        path="/api/v1/refresh-profiles/delete-batch",
        body={"ids": ids},
        resource_type="refresh_profile",
        audit_detail={"count": len(ids)},
        timeout=120,
    )


@api_router.put(
    "/instances/{instance_id}/refresh-profiles/enabled-batch",
    dependencies=[Depends(require_csrf)],
)
async def set_refresh_profiles_enabled_batch(
    instance_id: str,
    payload: AccountBatchEnabledRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    item = _get_instance(db, instance_id)
    ids = list(dict.fromkeys(value.strip() for value in payload.ids if value.strip()))
    if not ids:
        raise HTTPException(status_code=400, detail="ids is required")
    return await _remote_action(
        db,
        request,
        item,
        action="refresh_profile.enabled_batch",
        method="PUT",
        path="/api/v1/refresh-profiles/enabled-batch",
        body={"ids": ids, "enabled": payload.enabled},
        resource_type="refresh_profile",
        audit_detail={"count": len(ids), "enabled": payload.enabled},
        timeout=120,
    )


@api_router.post("/instances/{instance_id}/refresh-profiles/export", dependencies=[Depends(require_csrf)])
async def export_refresh_profiles(instance_id: str, request: Request, body: dict = Body(default={}), db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    data = await _remote_action(
        db, request, item, action="refresh_profile.export", method="POST",
        path="/api/v1/refresh-profiles/export-cookies", body={"ids": body.get("ids")},
        resource_type="refresh_profile", audit_detail={"count": len(body.get("ids") or [])},
    )
    content = json.dumps(data, ensure_ascii=False, indent=2).encode()
    return StreamingResponse(
        iter([content]), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{item.name}-cookies.json"'},
    )


@api_router.post("/instances/{instance_id}/refresh-profiles/{profile_id}/refresh", dependencies=[Depends(require_csrf)])
async def refresh_profile(instance_id: str, profile_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="refresh_profile.refresh", method="POST",
        path=f"/api/v1/refresh-profiles/{profile_id}/refresh-now",
        resource_type="refresh_profile", resource_id=profile_id, timeout=60,
    )


@api_router.put("/instances/{instance_id}/refresh-profiles/{profile_id}/enabled", dependencies=[Depends(require_csrf)])
async def set_refresh_profile_enabled(
    instance_id: str, profile_id: str, enabled: bool, request: Request, db: Session = Depends(get_db)
):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="refresh_profile.enabled", method="PUT",
        path=f"/api/v1/refresh-profiles/{profile_id}/enabled", body={"enabled": enabled},
        resource_type="refresh_profile", resource_id=profile_id, audit_detail={"enabled": enabled},
    )


@api_router.delete("/instances/{instance_id}/refresh-profiles/{profile_id}", dependencies=[Depends(require_csrf)])
async def delete_refresh_profile(instance_id: str, profile_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="refresh_profile.delete", method="DELETE",
        path=f"/api/v1/refresh-profiles/{profile_id}", resource_type="refresh_profile", resource_id=profile_id,
    )


@api_router.get("/instances/{instance_id}/config")
async def get_remote_config(instance_id: str, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    _ensure_compatible(item, "config")
    try:
        return (await remote_client.request(item.base_url, "GET", "/api/v1/config")).data
    except RemoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.put("/instances/{instance_id}/config", dependencies=[Depends(require_csrf)])
async def update_remote_config(instance_id: str, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="config.update", method="PUT", path="/api/v1/config",
        body=body, resource_type="config", audit_detail={"fields": sorted(body)},
    )


@api_router.get("/logs")
async def aggregate_logs(
    instance_ids: Optional[str] = None,
    cursor: str = "",
    limit: int = Query(default=100, ge=1, le=200),
    prompt: str = Query(default="", max_length=300),
    errors_only: bool = False,
    db: Session = Depends(get_db),
):
    instances = _selected_instances(db, instance_ids)
    cursors = _decode_cursor(cursor)

    async def fetch(item: Instance):
        if cursors.get(item.id) == 0:
            return item, {"items": [], "next_before_ts": None}, None
        try:
            _ensure_compatible(item, "cursor_logs")
            data = await remote_client.logs(
                item.base_url,
                before_ts=cursors.get(item.id),
                limit=limit,
                prompt=prompt,
                errors_only=errors_only,
            )
            return item, data, None
        except (RemoteError, HTTPException) as exc:
            return item, None, str(exc)

    results = await asyncio.gather(*(fetch(item) for item in instances))
    merged: list[dict[str, Any]] = []
    errors = []
    source_items: dict[str, list[dict[str, Any]]] = {}
    source_next: dict[str, Optional[float]] = {}
    for instance, data, error in results:
        if error:
            errors.append({"instance_id": instance.id, "instance_name": instance.name, "detail": error})
            continue
        rows = data.get("items", []) if isinstance(data, dict) else []
        source_items[instance.id] = rows
        source_next[instance.id] = data.get("next_before_ts") if isinstance(data, dict) else None
        for row in rows:
            merged.append({**row, "instance_id": instance.id, "instance_name": instance.name})
    merged.sort(key=lambda row: float(row.get("ts") or 0), reverse=True)
    page = merged[:limit]
    included: dict[str, list[dict[str, Any]]] = {}
    for row in page:
        included.setdefault(str(row.get("instance_id")), []).append(row)

    next_map = dict(cursors)
    has_more = False
    for instance in instances:
        rows = source_items.get(instance.id, [])
        used = included.get(instance.id, [])
        if used:
            next_map[instance.id] = min(float(row.get("ts") or 0) for row in used)
        elif not rows and source_next.get(instance.id) is None:
            next_map[instance.id] = 0.0
        if len(rows) > len(used) or source_next.get(instance.id) is not None:
            has_more = True
    return {
        "status": "partial" if errors else "ok",
        "items": page,
        "errors": errors,
        "next_cursor": _encode_cursor(next_map) if has_more else None,
    }


@api_router.get("/instances/{instance_id}/logs/errors/{code}")
async def remote_error_detail(instance_id: str, code: str, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    _ensure_compatible(item, "cursor_logs")
    try:
        return (
            await remote_client.request(item.base_url, "GET", f"/api/v1/logs/errors/{code}")
        ).data
    except RemoteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.delete("/instances/{instance_id}/logs", dependencies=[Depends(require_csrf)])
async def clear_remote_logs(instance_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_instance(db, instance_id)
    return await _remote_action(
        db, request, item, action="logs.clear", method="DELETE", path="/api/v1/logs", resource_type="logs",
    )


@api_router.get("/instances/{instance_id}/generated/{filename}")
async def generated_media(instance_id: str, filename: str, db: Session = Depends(get_db)):
    if not filename or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="File not found")
    item = _get_instance(db, instance_id)
    _ensure_compatible(item, "generated_media")
    client = httpx.AsyncClient(timeout=60, follow_redirects=False)
    request = client.build_request(
        "GET", f"{item.base_url.rstrip('/')}/generated/{filename}", headers=remote_client.headers
    )
    try:
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Media fetch failed: {exc}") from exc
    if response.status_code != 200:
        await response.aclose()
        await client.aclose()
        raise HTTPException(status_code=response.status_code, detail="Media fetch failed")

    async def stream():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(stream(), media_type=response.headers.get("content-type", "application/octet-stream"))


@api_router.get("/alerts")
def list_alerts(
    state: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = select(AlertEvent).order_by(AlertEvent.updated_at.desc()).limit(limit)
    if state:
        query = query.where(AlertEvent.state == state)
    rows = db.scalars(query).all()
    instances = {item.id: item for item in db.scalars(select(Instance)).all()}
    rules = {item.id: item for item in db.scalars(select(AlertRule)).all()}
    return {
        "alerts": [
            {
                "id": row.id,
                "instance_id": row.instance_id,
                "instance_name": instances.get(row.instance_id).name if instances.get(row.instance_id) else "Deleted",
                "rule_id": row.rule_id,
                "rule_name": rules.get(row.rule_id).name if rules.get(row.rule_id) else row.rule_id,
                "state": row.state,
                "severity": row.severity,
                "message": row.message,
                "value": row.value,
                "opened_at": row.opened_at,
                "firing_at": row.firing_at,
                "resolved_at": row.resolved_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    }


@api_router.get("/alert-rules")
def list_alert_rules(db: Session = Depends(get_db)):
    seed_alert_rules(db)
    return {
        "rules": [
            {
                "id": row.id,
                "name": row.name,
                "severity": row.severity,
                "enabled": row.enabled,
                "threshold": row.threshold,
                "minimum_requests": row.minimum_requests,
                "pending_samples": row.pending_samples,
                "recovery_samples": row.recovery_samples,
            }
            for row in db.scalars(select(AlertRule).order_by(AlertRule.id)).all()
        ]
    }


@api_router.put("/alert-rules/{rule_id}", dependencies=[Depends(require_csrf)])
def update_alert_rule(
    rule_id: str, payload: AlertRuleUpdate, request: Request, db: Session = Depends(get_db)
):
    started = time.perf_counter()
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(rule, key, value)
    db.commit()
    _record_audit(
        db, request=request, instance_id=None, action="alert_rule.update", outcome="success",
        started=started, resource_type="alert_rule", resource_id=rule_id,
        detail={"fields": sorted(changes)},
    )
    return {"status": "ok"}


@api_router.post("/instances/{instance_id}/silences", dependencies=[Depends(require_csrf)])
def silence_instance(
    instance_id: str, payload: SilenceRequest, request: Request, db: Session = Depends(get_db)
):
    started = time.perf_counter()
    _get_instance(db, instance_id)
    if payload.rule_id and db.get(AlertRule, payload.rule_id) is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    silence = AlertSilence(
        instance_id=instance_id,
        rule_id=payload.rule_id,
        starts_at=time.time(),
        ends_at=time.time() + payload.duration_seconds,
        reason=payload.reason,
    )
    db.add(silence)
    db.commit()
    db.refresh(silence)
    _record_audit(
        db, request=request, instance_id=instance_id, action="alert.silence", outcome="success",
        started=started, resource_type="silence", resource_id=str(silence.id),
        detail={"duration_seconds": payload.duration_seconds, "rule_id": payload.rule_id},
    )
    return {"status": "ok", "id": silence.id, "ends_at": silence.ends_at}


@api_router.get("/audit")
def audit_events(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.ts.desc()).limit(limit)).all()
    instances = {item.id: item.name for item in db.scalars(select(Instance)).all()}
    return {
        "events": [
            {
                "id": row.id,
                "ts": row.ts,
                "instance_id": row.instance_id,
                "instance_name": instances.get(row.instance_id or "", "-"),
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "outcome": row.outcome,
                "duration_seconds": row.duration_seconds,
                "request_id": row.request_id,
                "detail": row.detail,
            }
            for row in rows
        ]
    }


@api_router.get("/settings")
def manager_settings(db: Session = Depends(get_db)):
    return {
        "poll_interval_seconds": settings.poll_interval_seconds,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "metrics_retention_days": settings.metrics_retention_days,
        "event_retention_days": settings.event_retention_days,
        "ops_key_configured": bool(settings.ops_key),
        "low_credit_threshold": get_low_credit_threshold(db),
        **notification_service.status(),
    }


@api_router.put("/settings/preferences", dependencies=[Depends(require_csrf)])
async def update_manager_preferences(
    payload: ManagerPreferencesUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    previous = get_low_credit_threshold(db)
    current = set_low_credit_threshold(db, payload.low_credit_threshold)
    _record_audit(
        db,
        request=request,
        instance_id=None,
        action="settings.low_credit_threshold",
        outcome="success",
        started=started,
        resource_type="settings",
        resource_id="low_credit_threshold",
        detail={"previous": previous, "current": current},
    )
    await fleet_poller.run_once()
    return {"status": "ok", "low_credit_threshold": current}


@api_router.post("/settings/notifications/test", dependencies=[Depends(require_csrf)])
async def test_notifications(request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    errors = await notification_service.send_test()
    outcome = "success" if not errors else "failed"
    _record_audit(
        db, request=request, instance_id=None, action="notification.test", outcome=outcome,
        started=started, resource_type="notification", detail={"error_count": len(errors)},
    )
    if errors:
        return JSONResponse(status_code=502, content={"status": "failed", "errors": errors})
    return {"status": "ok"}


@api_router.post("/poll", dependencies=[Depends(require_csrf)])
async def poll_now(request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    await fleet_poller.run_once()
    _record_audit(
        db, request=request, instance_id=None, action="fleet.poll", outcome="success",
        started=started, resource_type="fleet",
    )
    return {"status": "ok"}
