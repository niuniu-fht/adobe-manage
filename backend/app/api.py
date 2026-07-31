import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .alerts import seed_alert_rules
from .auto_replacements import auto_replacement_service
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
from .preferences import (
    get_account_targets,
    get_low_credit_threshold,
    set_account_targets,
    set_low_credit_threshold,
)
from .remote import RemoteError, remote_client
from .replacement_coordinator import replacement_coordinator
from .safe_replacements import TERMINAL_STATUSES, safe_replacement_operations
from .schemas import (
    AccountBatchEnabledRequest,
    AccountMoveRequest,
    AccountBatchRequest,
    AccountSafeReplaceRequest,
    AlertRuleUpdate,
    FleetCookieImportRequest,
    FleetLowCreditDeleteRequest,
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
    require_ops_key,
    verify_access_key,
)
from .taem import TaemError, taem_client


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
api_router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
integration_router = APIRouter(
    prefix="/api/integration",
    dependencies=[Depends(require_ops_key)],
)


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
    total_success = 0
    total_in_progress = 0
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
        snapshot = instance.last_snapshot if isinstance(instance.last_snapshot, dict) else {}
        request_stats = snapshot.get("requests") if isinstance(snapshot, dict) else {}
        request_stats = request_stats if isinstance(request_stats, dict) else {}
        today_stats = request_stats.get("today")
        today_stats = today_stats if isinstance(today_stats, dict) else {}
        success_value = today_stats.get("successful")
        if success_value is None:
            success_value = request_stats.get("successful")
        try:
            total_success += max(0, int(success_value or 0))
        except (TypeError, ValueError):
            pass
        try:
            total_in_progress += max(0, int(request_stats.get("in_progress") or 0))
        except (TypeError, ValueError):
            pass
        payload.append(row)
    return {
        "instances": payload,
        "summary": {
            "total": len(instances),
            "online": sum(1 for item in instances if item.state == "online"),
            "offline": sum(1 for item in instances if item.state == "offline"),
            "active_alerts": len(active_alerts),
            "total_success": total_success,
            "total_in_progress": total_in_progress,
        },
        "preferences": {
            "low_credit_threshold": get_low_credit_threshold(db),
            "account_targets": get_account_targets(db),
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


@api_router.get("/auto-replacements")
def auto_replacements():
    return auto_replacement_service.snapshot()


@integration_router.get("/accounts")
async def integration_accounts(db: Session = Depends(get_db)):
    """Return the fleet account inventory for the mother-account service."""
    return await aggregate_accounts(None, db)


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


async def _fleet_account_states(
    instances: list[Instance], threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    async def fetch(item: Instance):
        try:
            accounts, summary = await _fetch_instance_accounts(item, threshold)
            try:
                total = int(summary.get("total"))
            except (TypeError, ValueError):
                total = len(accounts)
            return {
                "instance": item,
                "accounts": accounts,
                "summary": summary,
                "current_count": max(0, total),
            }, None
        except (RemoteError, HTTPException) as exc:
            return None, {
                "instance_id": item.id,
                "instance_name": item.name,
                "detail": str(exc)[:500],
            }

    results = await asyncio.gather(*(fetch(item) for item in instances))
    states = [state for state, error in results if state is not None and error is None]
    errors = [error for state, error in results if error is not None and state is None]
    return states, errors


def _allocate_fleet_import(
    items: list[dict[str, Any]], states: list[dict[str, Any]]
) -> None:
    for state in states:
        state["assigned"] = []
        state["deficit"] = max(
            0, int(state["target_count"]) - int(state["current_count"])
        )

    next_item = 0
    while next_item < len(items):
        assigned_in_round = False
        for state in states:
            if len(state["assigned"]) >= state["deficit"]:
                continue
            state["assigned"].append(items[next_item])
            next_item += 1
            assigned_in_round = True
            if next_item >= len(items):
                break
        if not assigned_in_round:
            break

    spill_index = 0
    while next_item < len(items):
        states[spill_index % len(states)]["assigned"].append(items[next_item])
        next_item += 1
        spill_index += 1


def _fleet_preflight_error(errors: list[dict[str, str]]) -> str:
    summary = "；".join(
        f'{item["instance_name"]}: {item["detail"]}' for item in errors[:5]
    )
    if len(errors) > 5:
        summary += f"；另有 {len(errors) - 5} 个实例失败"
    return f"读取实例账号失败，未执行变更：{summary}"


@api_router.post("/fleet/accounts/import", dependencies=[Depends(require_csrf)])
async def import_fleet_accounts(
    payload: FleetCookieImportRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    target_map: dict[str, int] = {}
    for target in payload.targets:
        if target.instance_id in target_map:
            raise HTTPException(status_code=400, detail="instance targets must be unique")
        target_map[target.instance_id] = target.target_count

    records = {
        item.id: item
        for item in db.scalars(
            select(Instance).where(Instance.id.in_(list(target_map)))
        ).all()
    }
    missing = [instance_id for instance_id in target_map if instance_id not in records]
    disabled = [records[value].name for value in target_map if value in records and not records[value].enabled]
    if missing:
        raise HTTPException(status_code=404, detail="one or more target instances were not found")
    if disabled:
        raise HTTPException(
            status_code=409,
            detail=f"target instances are disabled: {', '.join(disabled)}",
        )

    instances = [records[target.instance_id] for target in payload.targets]
    for instance in instances:
        _ensure_compatible(instance, "accounts")
        _ensure_compatible(instance, "refresh_profiles")
    states, errors = await _fleet_account_states(
        instances, get_low_credit_threshold(db)
    )
    if errors:
        _record_audit(
            db,
            request=request,
            instance_id=None,
            action="fleet.account_import",
            outcome="failed",
            started=started,
            resource_type="fleet_accounts",
            detail={"item_count": len(payload.items), "preflight_errors": len(errors)},
        )
        raise HTTPException(status_code=502, detail=_fleet_preflight_error(errors))

    for state in states:
        state["target_count"] = target_map[state["instance"].id]
    items = [item.model_dump(exclude_none=True) for item in payload.items]
    _allocate_fleet_import(items, states)
    set_account_targets(db, target_map)

    async def send(state: dict[str, Any]) -> dict[str, Any]:
        instance: Instance = state["instance"]
        assigned = state["assigned"]
        result = {
            "instance_id": instance.id,
            "instance_name": instance.name,
            "before_count": state["current_count"],
            "target_count": state["target_count"],
            "deficit": state["deficit"],
            "assigned_count": len(assigned),
            "imported_count": 0,
            "failed_count": 0,
            "refreshed_count": 0,
            "refresh_failed_count": 0,
            "status": "skipped" if not assigned else "pending",
            "error": "",
        }
        if not assigned:
            return result
        try:
            response = await remote_client.request(
                instance.base_url,
                "POST",
                "/api/v1/refresh-profiles/import-cookie-batch",
                json={"items": assigned},
                timeout=600,
            )
            data = response.data if isinstance(response.data, dict) else {}
            result.update(
                {
                    "imported_count": int(data.get("imported_count") or 0),
                    "failed_count": int(data.get("failed_count") or 0),
                    "refreshed_count": int(data.get("refreshed_count") or 0),
                    "refresh_failed_count": int(
                        data.get("refresh_failed_count") or 0
                    ),
                    "status": str(data.get("status") or "ok"),
                }
            )
        except RemoteError as exc:
            result["failed_count"] = len(assigned)
            result["status"] = "failed"
            result["error"] = str(exc)[:500]
        return result

    results = await asyncio.gather(*(send(state) for state in states))
    totals = {
        "total": len(items),
        "assigned": sum(item["assigned_count"] for item in results),
        "imported": sum(item["imported_count"] for item in results),
        "failed": sum(item["failed_count"] for item in results),
        "refreshed": sum(item["refreshed_count"] for item in results),
        "refresh_failed": sum(item["refresh_failed_count"] for item in results),
    }
    has_errors = bool(totals["failed"] or totals["refresh_failed"])
    status = "ok" if not has_errors else ("partial" if totals["imported"] else "failed")
    _record_audit(
        db,
        request=request,
        instance_id=None,
        action="fleet.account_import",
        outcome="success" if status == "ok" else status,
        started=started,
        resource_type="fleet_accounts",
        detail={
            "item_count": len(items),
            "instance_count": len(instances),
            "imported_count": totals["imported"],
            "failed_count": totals["failed"],
            "refresh_failed_count": totals["refresh_failed"],
            "targets": target_map,
        },
    )
    await fleet_poller.run_once()
    return {"status": status, **totals, "instances": results}


@api_router.post(
    "/fleet/accounts/delete-low-credit", dependencies=[Depends(require_csrf)]
)
async def delete_fleet_low_credit_accounts(
    payload: FleetLowCreditDeleteRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    instances = _selected_instances(db, None)
    if not instances:
        raise HTTPException(status_code=409, detail="no enabled instances")
    for instance in instances:
        _ensure_compatible(instance, "accounts")
        _ensure_compatible(instance, "refresh_profiles")
    states, errors = await _fleet_account_states(
        instances, payload.credit_threshold
    )
    if errors:
        _record_audit(
            db,
            request=request,
            instance_id=None,
            action="fleet.low_credit_delete",
            outcome="failed",
            started=started,
            resource_type="fleet_accounts",
            detail={
                "credit_threshold": payload.credit_threshold,
                "preflight_errors": len(errors),
            },
        )
        raise HTTPException(status_code=502, detail=_fleet_preflight_error(errors))

    for state in states:
        state["matched_ids"] = [
            account["id"]
            for account in state["accounts"]
            if account.get("credits_available") is not None
            and float(account["credits_available"]) < payload.credit_threshold
        ]

    async def remove(state: dict[str, Any]) -> dict[str, Any]:
        instance: Instance = state["instance"]
        matched_ids = state["matched_ids"]
        result = {
            "instance_id": instance.id,
            "instance_name": instance.name,
            "matched_count": len(matched_ids),
            "deleted_count": 0,
            "missing_count": 0,
            "status": "skipped" if not matched_ids else "pending",
            "error": "",
        }
        if not matched_ids:
            return result
        try:
            response = await remote_client.request(
                instance.base_url,
                "POST",
                "/api/v1/refresh-profiles/delete-batch",
                json={"ids": matched_ids},
                timeout=180,
            )
            data = response.data if isinstance(response.data, dict) else {}
            result.update(
                {
                    "deleted_count": int(data.get("deleted_count") or 0),
                    "missing_count": int(data.get("missing_count") or 0),
                    "status": str(data.get("status") or "ok"),
                }
            )
        except RemoteError as exc:
            result["status"] = "failed"
            result["error"] = str(exc)[:500]
        return result

    results = await asyncio.gather(*(remove(state) for state in states))
    matched = sum(item["matched_count"] for item in results)
    deleted_count = sum(item["deleted_count"] for item in results)
    missing_count = sum(item["missing_count"] for item in results)
    failed_instances = sum(1 for item in results if item["status"] == "failed")
    status = "ok" if not missing_count and not failed_instances else "partial"
    _record_audit(
        db,
        request=request,
        instance_id=None,
        action="fleet.low_credit_delete",
        outcome="success" if status == "ok" else status,
        started=started,
        resource_type="fleet_accounts",
        detail={
            "credit_threshold": payload.credit_threshold,
            "matched_count": matched,
            "deleted_count": deleted_count,
            "missing_count": missing_count,
            "failed_instances": failed_instances,
        },
    )
    await fleet_poller.run_once()
    return {
        "status": status,
        "credit_threshold": payload.credit_threshold,
        "matched_count": matched,
        "deleted_count": deleted_count,
        "missing_count": missing_count,
        "failed_instances": failed_instances,
        "instances": results,
    }


IMAGE_QUEUE_STATES = {
    "QUEUED",
    "UPLOADING",
    "SUBMITTING",
    "WAITING_POLL",
    "RATE_LIMITED",
    "DOWNLOADING",
    "DOWNLOAD_RETRY",
    "COMPLETED",
    "FAILED",
}


def _queue_number(value: Any, *, integer: bool = False) -> float | int:
    try:
        number = max(0.0, float(value or 0))
    except (TypeError, ValueError):
        number = 0.0
    return int(number) if integer else round(number, 3)


def _queue_state(value: Any) -> str:
    state = str(value or "QUEUED").strip().upper()
    return state if state in IMAGE_QUEUE_STATES else "QUEUED"


def _safe_queue_output(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return {
        "index": _queue_number(value.get("index"), integer=True),
        "state": _queue_state(value.get("state")),
        "token_id": str(value.get("token_id") or "")[:80] or None,
        "account_name": str(value.get("account_name") or "")[:160] or None,
        "upstream_job_id": str(value.get("upstream_job_id") or "")[:200] or None,
        "retry_count": _queue_number(value.get("retry_count"), integer=True),
        "next_run_at": _optional_float(value.get("next_run_at")),
        "rate_limit_wait_seconds": _queue_number(
            value.get("rate_limit_wait_seconds")
        ),
        "download_attempt": _queue_number(
            value.get("download_attempt"), integer=True
        ),
        "last_error": str(value.get("last_error") or "")[:500] or None,
        "updated_at": _optional_float(value.get("updated_at")),
    }


def _safe_queue_request(value: Any, instance: Instance) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    request_id = str(value.get("id") or value.get("log_id") or "").strip()
    if not request_id:
        return None
    outputs = [
        output
        for output in (
            _safe_queue_output(raw) for raw in (value.get("outputs") or [])
        )
        if output is not None
    ]
    return {
        "id": request_id[:200],
        "log_id": str(value.get("log_id") or request_id)[:200],
        "instance_id": instance.id,
        "instance_name": instance.name,
        "instance_location": instance.location,
        "path": str(value.get("path") or "")[:300],
        "model": str(value.get("model") or "")[:160],
        "prompt_preview": str(value.get("prompt_preview") or "")[:180],
        "requested_count": _queue_number(
            value.get("requested_count"), integer=True
        ),
        "completed_count": _queue_number(
            value.get("completed_count"), integer=True
        ),
        "state": _queue_state(value.get("state")),
        "created_at": _optional_float(value.get("created_at")),
        "updated_at": _optional_float(value.get("updated_at")),
        "finished_at": _optional_float(value.get("finished_at")),
        "elapsed_seconds": _queue_number(value.get("elapsed_seconds")),
        "error": str(value.get("error") or "")[:1000] or None,
        "outputs": outputs,
    }


@api_router.get("/image-queue")
async def aggregate_image_queue(
    instance_ids: Optional[str] = None,
    limit_per_instance: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    instances = _selected_instances(db, instance_ids)

    async def fetch(item: Instance):
        try:
            _ensure_compatible(item, "image_queue")
            data = await remote_client.image_queue(
                item.base_url, limit=limit_per_instance
            )
            return item, data, None
        except (RemoteError, HTTPException) as exc:
            return item, None, str(exc)

    results = await asyncio.gather(*(fetch(item) for item in instances))
    summary = {
        "instances": len(instances),
        "instances_ok": 0,
        "instances_error": 0,
        "requests": 0,
        "outputs": 0,
        "in_progress": 0,
        "queued": 0,
        "waiting_poll": 0,
        "rate_limited": 0,
        "download_retry": 0,
    }
    items: list[dict[str, Any]] = []
    instance_rows = []
    errors = []
    for instance, data, error in results:
        if error:
            summary["instances_error"] += 1
            detail = str(error)[:500]
            error_item = {
                "instance_id": instance.id,
                "instance_name": instance.name,
                "detail": detail,
            }
            errors.append(error_item)
            instance_rows.append(
                {
                    "instance_id": instance.id,
                    "instance_name": instance.name,
                    "state": "error",
                    "summary": {},
                    "error": detail,
                }
            )
            continue

        summary["instances_ok"] += 1
        remote_summary = (
            data.get("summary") if isinstance(data.get("summary"), dict) else {}
        )
        safe_summary = {
            key: _queue_number(remote_summary.get(key), integer=True)
            for key in (
                "requests",
                "outputs",
                "in_progress",
                "queued",
                "waiting_poll",
                "rate_limited",
                "download_retry",
            )
        }
        for key, value in safe_summary.items():
            summary[key] += value
        instance_rows.append(
            {
                "instance_id": instance.id,
                "instance_name": instance.name,
                "state": "ok",
                "summary": safe_summary,
                "error": "",
            }
        )
        for raw in data.get("items") or []:
            safe_item = _safe_queue_request(raw, instance)
            if safe_item is not None:
                items.append(safe_item)

    items.sort(
        key=lambda item: float(item.get("created_at") or 0), reverse=True
    )
    return {
        "status": "partial" if errors else "ok",
        "summary": summary,
        "instances": instance_rows,
        "items": items,
        "errors": errors,
        "updated_at": time.time(),
    }


@api_router.post(
    "/fleet/tokens/credits-batch", dependencies=[Depends(require_csrf)]
)
async def refresh_fleet_credits(
    request: Request,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    instances = _selected_instances(db, None)

    async def refresh(item: Instance) -> dict[str, Any]:
        try:
            _ensure_compatible(item, "tokens")
            response = await remote_client.request(
                item.base_url,
                "POST",
                "/api/v1/tokens/credits/refresh-batch",
                json={"ids": None},
                timeout=180,
            )
            data = response.data if isinstance(response.data, dict) else {}
            failed_count = _queue_number(
                data.get("failed_count"), integer=True
            )
            return {
                "instance_id": item.id,
                "instance_name": item.name,
                "status": "partial" if failed_count else "ok",
                "total": _queue_number(data.get("total"), integer=True),
                "refreshed_count": _queue_number(
                    data.get("refreshed_count"), integer=True
                ),
                "failed_count": failed_count,
                "error": "",
            }
        except (RemoteError, HTTPException) as exc:
            return {
                "instance_id": item.id,
                "instance_name": item.name,
                "status": "failed",
                "total": 0,
                "refreshed_count": 0,
                "failed_count": 0,
                "error": str(exc)[:500],
            }

    results = await asyncio.gather(*(refresh(item) for item in instances))
    refreshed_count = sum(int(item["refreshed_count"]) for item in results)
    failed_count = sum(int(item["failed_count"]) for item in results)
    failed_instances = sum(1 for item in results if item["status"] == "failed")
    partial_instances = sum(1 for item in results if item["status"] == "partial")
    succeeded_instances = len(results) - failed_instances - partial_instances
    status = "partial" if failed_instances or partial_instances or failed_count else "ok"
    _record_audit(
        db,
        request=request,
        instance_id=None,
        action="fleet.credits_refresh",
        outcome="success" if status == "ok" else "partial",
        started=started,
        resource_type="fleet",
        detail={
            "instance_count": len(results),
            "succeeded_instances": succeeded_instances,
            "partial_instances": partial_instances,
            "failed_instances": failed_instances,
            "refreshed_count": refreshed_count,
            "failed_count": failed_count,
        },
    )
    if results:
        await fleet_poller.run_once()
    return {
        "status": status,
        "summary": {
            "instances": len(results),
            "succeeded_instances": succeeded_instances,
            "partial_instances": partial_instances,
            "failed_instances": failed_instances,
            "refreshed_count": refreshed_count,
            "failed_count": failed_count,
        },
        "instances": results,
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
    "/instances/{instance_id}/refresh-profiles/{profile_id}/replace-safe",
    dependencies=[Depends(require_csrf)],
)
async def replace_refresh_profile_safely(
    instance_id: str,
    profile_id: str,
    payload: AccountSafeReplaceRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    instance = await _safe_replace_preflight(
        instance_id, profile_id, payload.email, request, db, started
    )
    lock_owner = f"manual-sync:{_request_id(request)}"
    if not replacement_coordinator.try_acquire(lock_owner):
        raise HTTPException(
            status_code=409,
            detail="已有移除补号流程运行中，请等待控制台任务结束",
        )
    try:
        try:
            replacement = await taem_client.replace_member(payload.email)
        except TaemError as exc:
            _record_safe_replace_failure(
                db, request, instance, profile_id, started, "taem", str(exc)
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=f"母号移除并安全补号失败：{exc}",
            ) from exc
        return await _finalize_safe_replacement(
            instance=instance,
            profile_id=profile_id,
            source_email=payload.email,
            replacement=replacement,
            request=request,
            db=db,
            started=started,
        )
    finally:
        replacement_coordinator.release(lock_owner)


def _release_safe_replacement_lock(operation) -> None:
    if operation.status in TERMINAL_STATUSES and operation.lock_owner:
        replacement_coordinator.release(operation.lock_owner)
        operation.lock_owner = ""


def _record_safe_replace_failure(
    db: Session,
    request: Request,
    instance: Instance,
    profile_id: str,
    started: float,
    stage: str,
    error: str,
) -> None:
    _record_audit(
        db,
        request=request,
        instance_id=instance.id,
        action="refresh_profile.replace_safe",
        outcome="failed",
        started=started,
        resource_type="refresh_profile",
        resource_id=profile_id,
        detail={"failed_stage": stage, "error": str(error)[:300]},
    )


async def _safe_replace_preflight(
    instance_id: str,
    profile_id: str,
    source_email: str,
    request: Request,
    db: Session,
    started: float,
) -> Instance:
    instance = _get_instance(db, instance_id)
    _ensure_compatible(instance, "refresh_profiles")
    try:
        accounts_data = await remote_client.accounts(
            instance.base_url, get_low_credit_threshold(db)
        )
    except RemoteError as exc:
        _record_safe_replace_failure(
            db, request, instance, profile_id, started, "preflight", str(exc)
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"读取 Adobe 实例账号失败，未执行母号变更：{exc}",
        ) from exc

    source_profile = next(
        (
            item
            for item in (accounts_data.get("items") or [])
            if isinstance(item, dict)
            and str(item.get("id") or "").strip() == profile_id
        ),
        None,
    )
    source_profile_email = ""
    if isinstance(source_profile, dict):
        source_profile_email = str(source_profile.get("email") or "").strip().lower()
        source_profile_name = str(source_profile.get("name") or "").strip().lower()
        if not source_profile_email and "@" in source_profile_name:
            source_profile_email = source_profile_name
    if not source_profile_email or source_profile_email != source_email:
        detail = (
            "所选 Cookie 账号已不存在，请刷新列表后重试"
            if source_profile is None
            else "所选 Cookie 账号邮箱已变化，请刷新列表后重试"
        )
        _record_safe_replace_failure(
            db, request, instance, profile_id, started, "preflight", detail
        )
        raise HTTPException(status_code=409, detail=detail)
    return instance


async def _finalize_safe_replacement(
    *,
    instance: Instance,
    profile_id: str,
    source_email: str,
    replacement: dict[str, Any],
    request: Request,
    db: Session,
    started: float,
    phase_update: Optional[Callable[[str, str], None]] = None,
) -> dict[str, Any]:
    audit_detail: dict[str, Any] = {"failed_stage": ""}
    cookie = str(replacement.get("cookie") or "").strip()
    replacement_email = str(replacement.get("replacement_email") or "").strip().lower()
    if not cookie:
        detail = "母号补号流程结束，但未返回新 Cookie"
        _record_safe_replace_failure(
            db, request, instance, profile_id, started, "taem", detail
        )
        raise HTTPException(status_code=502, detail=detail)
    if phase_update:
        phase_update("importing", "母号流程已完成，开始将新 Cookie 回写 Adobe 实例")
    try:
        import_response = await remote_client.request(
            instance.base_url,
            "POST",
            "/api/v1/refresh-profiles/import-cookie-batch",
            json={
                "items": [
                    {
                        "cookie": {"cookie": cookie},
                        "name": replacement_email or source_email,
                    }
                ]
            },
            timeout=600,
        )
    except RemoteError as exc:
        _record_safe_replace_failure(
            db, request, instance, profile_id, started, "import", str(exc)
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=(
                "母号已完成移除和补号，但新 Cookie 导入 Adobe 实例失败；"
                f"旧 Cookie 账号仍保留：{exc}"
            ),
        ) from exc

    import_data = import_response.data if isinstance(import_response.data, dict) else {}
    imported = bool(_confirmed_import_indices(import_data, 1))
    if not imported:
        failed_items = import_data.get("failed") if isinstance(import_data.get("failed"), list) else []
        failed_detail = next(
            (
                str(item.get("detail") or "").strip()
                for item in failed_items
                if isinstance(item, dict) and str(item.get("detail") or "").strip()
            ),
            "实例未确认导入结果",
        )
        _record_safe_replace_failure(
            db, request, instance, profile_id, started, "import", failed_detail
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "母号已完成移除和补号，但新 Cookie 导入 Adobe 实例失败；"
                f"旧 Cookie 账号仍保留：{failed_detail}"
            ),
        )

    imported_profiles = (
        import_data.get("profiles") if isinstance(import_data.get("profiles"), list) else []
    )
    replacement_profile_id = next(
        (
            str(item.get("id") or "").strip()
            for item in imported_profiles
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ),
        "",
    )
    try:
        refresh_failed_count = max(0, int(import_data.get("refresh_failed_count") or 0))
    except (TypeError, ValueError):
        refresh_failed_count = 0
    if phase_update:
        phase_update("cleanup", "新 Cookie 已导入，开始确认并清理旧 Cookie 账号")

    old_profile_removed = False
    cleanup_error = ""
    if not replacement_profile_id:
        cleanup_error = "实例未返回新账号 ID，已保留旧 Cookie 账号"
    elif replacement_profile_id == profile_id:
        old_profile_removed = True
    else:
        try:
            delete_response = await remote_client.request(
                instance.base_url,
                "POST",
                "/api/v1/refresh-profiles/delete-batch",
                json={"ids": [profile_id]},
                timeout=120,
            )
            delete_data = (
                delete_response.data if isinstance(delete_response.data, dict) else {}
            )
            old_profile_removed = profile_id in _confirmed_removed_ids(
                delete_data, [profile_id]
            )
            if not old_profile_removed:
                cleanup_error = "实例未确认旧 Cookie 账号已清理"
        except RemoteError as exc:
            cleanup_error = f"旧 Cookie 账号清理失败：{exc}"

    issues = []
    if refresh_failed_count:
        issues.append("新 Cookie 已导入，但首次刷新失败")
    if cleanup_error:
        issues.append(cleanup_error)
    result_status = "partial" if issues else "ok"
    message = "；".join(issues) if issues else "已完成移除、安全补号和 Cookie 回写"
    audit_detail.update(
        {
            "failed_stage": "cleanup" if cleanup_error else ("refresh" if refresh_failed_count else ""),
            "imported_count": 1,
            "refresh_failed_count": refresh_failed_count,
            "old_profile_removed": old_profile_removed,
        }
    )
    _record_audit(
        db,
        request=request,
        instance_id=instance.id,
        action="refresh_profile.replace_safe",
        outcome="success" if result_status == "ok" else "partial",
        started=started,
        resource_type="refresh_profile",
        resource_id=profile_id,
        detail=audit_detail,
    )
    try:
        await fleet_poller.run_once()
    except Exception:
        pass
    return {
        "status": result_status,
        "message": message,
        "source_email": source_email,
        "replacement_email": replacement_email,
        "replacement_profile_id": replacement_profile_id,
        "imported_count": 1,
        "refresh_failed_count": refresh_failed_count,
        "old_profile_removed": old_profile_removed,
    }


@api_router.post(
    "/instances/{instance_id}/refresh-profiles/{profile_id}/replace-safe/start",
    dependencies=[Depends(require_csrf)],
)
async def start_safe_replacement(
    instance_id: str,
    profile_id: str,
    payload: AccountSafeReplaceRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    existing = safe_replacement_operations.find_active(instance_id, profile_id)
    if existing:
        if existing.source_email != payload.email:
            raise HTTPException(status_code=409, detail="该账号已有另一个补号流程正在运行")
        return existing.snapshot()

    started = time.perf_counter()
    instance = await _safe_replace_preflight(
        instance_id, profile_id, payload.email, request, db, started
    )
    operation = safe_replacement_operations.create(
        instance_id=instance.id,
        instance_name=instance.name,
        profile_id=profile_id,
        source_email=payload.email,
        request_id=_request_id(request),
    )
    operation.lock_owner = f"manual:{operation.id}"
    if not replacement_coordinator.try_acquire(operation.lock_owner):
        operation.status = "failed"
        operation.phase = "failed"
        operation.error = "已有移除补号流程运行中"
        operation.add_log(operation.error)
        _release_safe_replacement_lock(operation)
        raise HTTPException(
            status_code=409,
            detail="已有移除补号流程运行中，请等待控制台任务结束",
        )
    operation.started_at = started
    operation.add_log(f"已确认目标账号 {payload.email}，正在连接母号系统")
    try:
        upstream = await taem_client.start_replace_member(payload.email)
        operation.upstream_job_id = int(upstream.get("id"))
    except (TaemError, TypeError, ValueError) as exc:
        status_code = exc.status_code if isinstance(exc, TaemError) else 502
        operation.status = "failed"
        operation.error = str(exc)
        operation.add_log(f"启动母号任务失败：{exc}")
        _record_safe_replace_failure(
            db, request, instance, profile_id, started, "taem", str(exc)
        )
        _release_safe_replacement_lock(operation)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    operation.status = "running"
    operation.phase = "pulling"
    operation.target = max(1, int(upstream.get("target") or 1))
    operation.success = max(0, int(upstream.get("success") or 0))
    operation.fail = max(0, int(upstream.get("fail") or 0))
    upstream_logs = upstream.get("logs") if isinstance(upstream.get("logs"), list) else []
    for line in upstream_logs:
        operation.add_log(str(line))
    operation.upstream_log_offset = max(
        len(upstream_logs), int(upstream.get("log_total") or 0)
    )
    operation.add_log(f"母号任务 #{operation.upstream_job_id} 已启动")
    return operation.snapshot()


@api_router.post(
    "/safe-replacements/{operation_id}/poll",
    dependencies=[Depends(require_csrf)],
)
async def poll_safe_replacement(
    operation_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    operation = safe_replacement_operations.get(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="补号流程不存在或 Manager 已重启")
    if operation.status in TERMINAL_STATUSES:
        _release_safe_replacement_lock(operation)
        return operation.snapshot()
    if not safe_replacement_operations.begin_poll(operation):
        return operation.snapshot()

    try:
        if operation.upstream_job_id is None:
            return operation.snapshot()
        try:
            upstream = await taem_client.get_job(
                operation.upstream_job_id,
                log_offset=operation.upstream_log_offset,
            )
        except TaemError as exc:
            operation.status = "failed"
            operation.error = str(exc)
            operation.add_log(f"读取母号任务失败：{exc}")
            instance = _get_instance(db, operation.instance_id)
            _record_safe_replace_failure(
                db,
                request,
                instance,
                operation.profile_id,
                operation.started_at,
                "taem",
                str(exc),
            )
            return operation.snapshot()

        upstream_logs = upstream.get("logs") if isinstance(upstream.get("logs"), list) else []
        for line in upstream_logs:
            operation.add_log(str(line))
        operation.upstream_log_offset = max(
            operation.upstream_log_offset + len(upstream_logs),
            int(upstream.get("log_total") or 0),
        )
        operation.target = max(1, int(upstream.get("target") or operation.target))
        operation.success = max(0, int(upstream.get("success") or 0))
        operation.fail = max(0, int(upstream.get("fail") or 0))
        upstream_status = str(upstream.get("status") or "running")
        if upstream_status == "running":
            return operation.snapshot()

        instance = _get_instance(db, operation.instance_id)
        if upstream_status == "cancelled":
            operation.status = "cancelled"
            operation.error = "拉号任务已停止"
            operation.add_log("母号系统已停止本次拉号，未执行 Cookie 回写")
            _record_audit(
                db,
                request=request,
                instance_id=instance.id,
                action="refresh_profile.replace_safe",
                outcome="cancelled",
                started=operation.started_at,
                resource_type="refresh_profile",
                resource_id=operation.profile_id,
                detail={"failed_stage": "taem", "cancelled": True},
            )
            return operation.snapshot()
        if upstream_status != "done":
            error = str(upstream.get("error") or f"母号任务状态：{upstream_status}")
            operation.status = "failed"
            operation.error = error
            operation.add_log(f"母号任务结束：{error}")
            _record_safe_replace_failure(
                db,
                request,
                instance,
                operation.profile_id,
                operation.started_at,
                "taem",
                error,
            )
            return operation.snapshot()

        upstream_result = (
            upstream.get("result") if isinstance(upstream.get("result"), dict) else {}
        )
        replacement = (
            upstream_result.get("replacement")
            if isinstance(upstream_result.get("replacement"), dict)
            else {}
        )
        normalized_replacement = {
            "cookie": str(replacement.get("cookie") or ""),
            "replacement_email": str(replacement.get("email") or ""),
        }
        operation.status = "finalizing"
        operation.phase = "importing"

        def update_phase(phase: str, message: str) -> None:
            operation.phase = phase
            operation.add_log(message)

        try:
            result = await _finalize_safe_replacement(
                instance=instance,
                profile_id=operation.profile_id,
                source_email=operation.source_email,
                replacement=normalized_replacement,
                request=request,
                db=db,
                started=operation.started_at,
                phase_update=update_phase,
            )
        except HTTPException as exc:
            operation.status = "failed"
            operation.error = str(exc.detail)
            operation.add_log(str(exc.detail))
            return operation.snapshot()

        operation.result = result
        operation.status = "done" if result.get("status") == "ok" else "partial"
        operation.phase = "complete"
        operation.success = 1
        operation.add_log(str(result.get("message") or "移除并安全补号已完成"))
        return operation.snapshot()
    finally:
        safe_replacement_operations.end_poll(operation)
        _release_safe_replacement_lock(operation)


@api_router.post(
    "/safe-replacements/{operation_id}/cancel",
    dependencies=[Depends(require_csrf)],
)
async def cancel_safe_replacement(operation_id: str):
    operation = safe_replacement_operations.get(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="补号流程不存在或 Manager 已重启")
    if operation.status in TERMINAL_STATUSES:
        return operation.snapshot()
    if operation.phase not in {"starting", "pulling"}:
        raise HTTPException(status_code=409, detail="母号拉号已完成，正在回写 Cookie")
    if operation.upstream_job_id is None:
        raise HTTPException(status_code=409, detail="母号任务正在启动，请稍后停止")
    try:
        response = await taem_client.cancel_job(operation.upstream_job_id)
    except TaemError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    operation.cancel_requested = True
    operation.add_log(str(response.get("message") or "已请求停止拉号"))
    return operation.snapshot()


def _move_import_items(exported: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in exported:
        cookie_bundle: dict[str, Any] = {"cookie": str(item.get("cookie") or "")}
        headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
        session_id = next(
            (
                str(value or "").strip()
                for key, value in headers.items()
                if str(key or "").strip().lower() == "x-arp-session-id"
            ),
            "",
        )
        if session_id:
            cookie_bundle["headers"] = {"x-arp-session-id": session_id}
        items.append(
            {
                "cookie": cookie_bundle,
                "name": str(item.get("name") or "").strip() or None,
            }
        )
    return items


def _confirmed_import_indices(data: dict[str, Any], total: int) -> list[int]:
    failed_indices = {
        int(item["index"])
        for item in (data.get("failed") or [])
        if isinstance(item, dict)
        and isinstance(item.get("index"), int)
        and 0 <= item["index"] < total
    }
    try:
        imported_count = max(0, min(total, int(data.get("imported_count") or 0)))
    except (TypeError, ValueError):
        imported_count = 0
    candidates = [index for index in range(total) if index not in failed_indices]
    return candidates[:imported_count]


def _confirmed_removed_ids(data: dict[str, Any], attempted_ids: list[str]) -> set[str]:
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


@api_router.post(
    "/instances/{instance_id}/refresh-profiles/move",
    dependencies=[Depends(require_csrf)],
)
async def move_refresh_profiles(
    instance_id: str,
    payload: AccountMoveRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    source = _get_instance(db, instance_id)
    target = _get_instance(db, payload.target_instance_id)
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="source and target must be different")
    if not target.enabled:
        raise HTTPException(status_code=409, detail="target instance is disabled")
    _ensure_compatible(source, "refresh_profiles")
    _ensure_compatible(target, "refresh_profiles")
    ids = list(dict.fromkeys(value.strip() for value in payload.ids if value.strip()))
    if not ids:
        raise HTTPException(status_code=400, detail="ids is required")

    audit_detail: dict[str, Any] = {
        "source_instance_id": source.id,
        "source_instance_name": source.name,
        "target_instance_id": target.id,
        "target_instance_name": target.name,
        "requested_count": len(ids),
    }

    try:
        export_response = await remote_client.request(
            source.base_url,
            "POST",
            "/api/v1/refresh-profiles/export-cookies",
            json={"ids": ids},
            timeout=120,
        )
    except RemoteError as exc:
        audit_detail["failed_stage"] = "export"
        _record_audit(
            db,
            request=request,
            instance_id=source.id,
            action="refresh_profile.move_batch",
            outcome="failed",
            started=started,
            resource_type="refresh_profile",
            detail=audit_detail,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"源实例导出失败（HTTP {exc.status_code}）",
        ) from exc

    export_data = export_response.data if isinstance(export_response.data, dict) else {}
    raw_items = export_data.get("items") if isinstance(export_data.get("items"), list) else []
    exported_by_id = {
        str(item.get("id") or "").strip(): item
        for item in raw_items
        if isinstance(item, dict)
        and str(item.get("id") or "").strip() in ids
        and str(item.get("cookie") or "").strip()
    }
    exported = [exported_by_id[profile_id] for profile_id in ids if profile_id in exported_by_id]
    export_missing_count = len(ids) - len(exported)
    if not exported:
        audit_detail.update(
            {"exported_count": 0, "retained_count": len(ids), "failed_stage": "export"}
        )
        _record_audit(
            db,
            request=request,
            instance_id=source.id,
            action="refresh_profile.move_batch",
            outcome="failed",
            started=started,
            resource_type="refresh_profile",
            detail=audit_detail,
        )
        raise HTTPException(status_code=409, detail="源实例未导出任何所选 Cookie 账号")

    try:
        import_response = await remote_client.request(
            target.base_url,
            "POST",
            "/api/v1/refresh-profiles/import-cookie-batch",
            json={"items": _move_import_items(exported)},
            timeout=600,
        )
    except RemoteError as exc:
        audit_detail.update(
            {
                "exported_count": len(exported),
                "retained_count": len(ids),
                "failed_stage": "import",
            }
        )
        _record_audit(
            db,
            request=request,
            instance_id=source.id,
            action="refresh_profile.move_batch",
            outcome="failed",
            started=started,
            resource_type="refresh_profile",
            detail=audit_detail,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"目标实例导入失败，源账号保持不变（HTTP {exc.status_code}）",
        ) from exc

    import_data = import_response.data if isinstance(import_response.data, dict) else {}
    imported_indices = _confirmed_import_indices(import_data, len(exported))
    imported_profiles = (
        import_data.get("profiles") if isinstance(import_data.get("profiles"), list) else []
    )
    confirmed_pairs: list[tuple[int, str]] = []
    seen_target_ids: set[str] = set()
    for position, index in enumerate(imported_indices):
        if position >= len(imported_profiles):
            break
        profile = imported_profiles[position]
        target_profile_id = (
            str(profile.get("id") or "").strip() if isinstance(profile, dict) else ""
        )
        if not target_profile_id or target_profile_id in seen_target_ids:
            continue
        seen_target_ids.add(target_profile_id)
        confirmed_pairs.append((index, target_profile_id))
    imported_indices = [index for index, _target_id in confirmed_pairs]
    imported_source_ids = [
        str(exported[index].get("id") or "") for index in imported_indices
    ]
    target_ids_by_source = {
        str(exported[index].get("id") or ""): target_id
        for index, target_id in confirmed_pairs
    }

    removed_source_ids: set[str] = set()
    source_state_unknown_count = 0
    if imported_source_ids:
        try:
            delete_response = await remote_client.request(
                source.base_url,
                "POST",
                "/api/v1/refresh-profiles/delete-batch",
                json={"ids": imported_source_ids},
                timeout=120,
            )
            delete_data = (
                delete_response.data if isinstance(delete_response.data, dict) else {}
            )
            removed_source_ids = _confirmed_removed_ids(delete_data, imported_source_ids)
        except RemoteError:
            try:
                source_accounts = await remote_client.accounts(
                    source.base_url, get_low_credit_threshold(db)
                )
                remaining_ids = {
                    str(item.get("id") or "").strip()
                    for item in (source_accounts.get("items") or [])
                    if isinstance(item, dict)
                }
                removed_source_ids = {
                    profile_id
                    for profile_id in imported_source_ids
                    if profile_id not in remaining_ids
                }
            except (RemoteError, HTTPException):
                source_state_unknown_count = len(imported_source_ids)

    rollback_source_ids = [
        profile_id
        for profile_id in imported_source_ids
        if profile_id not in removed_source_ids and not source_state_unknown_count
    ]
    rollback_target_ids = [
        target_ids_by_source[profile_id]
        for profile_id in rollback_source_ids
        if profile_id in target_ids_by_source
    ]
    cleanup_failed_count = len(rollback_source_ids) - len(rollback_target_ids)
    if rollback_target_ids:
        try:
            rollback_response = await remote_client.request(
                target.base_url,
                "POST",
                "/api/v1/refresh-profiles/delete-batch",
                json={"ids": rollback_target_ids},
                timeout=120,
            )
            rollback_data = (
                rollback_response.data
                if isinstance(rollback_response.data, dict)
                else {}
            )
            rolled_back = _confirmed_removed_ids(rollback_data, rollback_target_ids)
            cleanup_failed_count += len(rollback_target_ids) - len(rolled_back)
        except RemoteError:
            cleanup_failed_count += len(rollback_target_ids)

    moved_count = len(removed_source_ids)
    retained_count = len(ids) - moved_count
    status = (
        "ok"
        if moved_count == len(ids) and not cleanup_failed_count
        else ("partial" if moved_count else "failed")
    )
    try:
        refresh_failed_count = max(0, int(import_data.get("refresh_failed_count") or 0))
    except (TypeError, ValueError):
        refresh_failed_count = 0
    result = {
        "status": status,
        "source": {"id": source.id, "name": source.name},
        "target": {"id": target.id, "name": target.name},
        "requested_count": len(ids),
        "exported_count": len(exported),
        "imported_count": len(imported_indices),
        "moved_count": moved_count,
        "retained_count": retained_count,
        "export_missing_count": export_missing_count,
        "import_failed_count": len(exported) - len(imported_indices),
        "refresh_failed_count": refresh_failed_count,
        "cleanup_failed_count": cleanup_failed_count,
        "source_state_unknown_count": source_state_unknown_count,
    }
    audit_detail.update(
        {
            key: result[key]
            for key in (
                "exported_count",
                "imported_count",
                "moved_count",
                "retained_count",
                "export_missing_count",
                "import_failed_count",
                "refresh_failed_count",
                "cleanup_failed_count",
                "source_state_unknown_count",
            )
        }
    )
    _record_audit(
        db,
        request=request,
        instance_id=source.id,
        action="refresh_profile.move_batch",
        outcome="success" if status == "ok" else status,
        started=started,
        resource_type="refresh_profile",
        detail=audit_detail,
    )
    try:
        await fleet_poller.run_once()
    except Exception:
        pass
    return result


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
        "account_targets": get_account_targets(db),
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
