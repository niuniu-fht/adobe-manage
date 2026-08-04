from app.database import SessionLocal
from app.models import AuditEvent, Instance
from app.remote import RemoteError, RemoteResponse, remote_client
from app.taem import TaemError, taem_client


def test_dashboard_aggregates_today_success_and_in_progress(authenticated):
    client, _headers = authenticated
    with SessionLocal() as db:
        db.add_all(
            [
                Instance(
                    name="East",
                    base_url="https://east.example",
                    state="online",
                    last_snapshot={
                        "requests": {
                            "successful": 99,
                            "in_progress": 2,
                            "today": {"successful": 12},
                        }
                    },
                ),
                Instance(
                    name="West",
                    base_url="https://west.example",
                    state="online",
                    last_snapshot={
                        "requests": {
                            "successful": 7,
                            "in_progress": 3,
                        }
                    },
                ),
            ]
        )
        db.commit()

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    assert response.json()["summary"]["total_success"] == 19
    assert response.json()["summary"]["total_in_progress"] == 5


def test_login_and_csrf_protect_mutations(client):
    unauthorized = client.get("/api/instances")
    login = client.post("/api/auth/login", json={"access_key": "manager-test-access"})
    missing_csrf = client.post(
        "/api/instances",
        json={"name": "East", "location": "Tokyo", "base_url": "https://east.example"},
    )

    assert unauthorized.status_code == 401
    assert login.status_code == 200
    assert missing_csrf.status_code == 403


def test_instance_crud(authenticated):
    client, headers = authenticated
    created = client.post(
        "/api/instances",
        headers=headers,
        json={
            "name": "East",
            "location": "Tokyo",
            "base_url": "https://east.example",
            "tags": ["production", "image"],
        },
    )
    instance_id = created.json()["id"]
    updated = client.put(
        f"/api/instances/{instance_id}",
        headers=headers,
        json={"location": "Osaka", "enabled": False},
    )
    listed = client.get("/api/instances")

    assert created.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["location"] == "Osaka"
    assert listed.json()["instances"][0]["enabled"] is False


def test_log_aggregation_returns_partial_results(authenticated, monkeypatch):
    client, headers = authenticated
    with SessionLocal() as db:
        first = Instance(name="East", base_url="https://east.example", location="Tokyo")
        second = Instance(name="West", base_url="https://west.example", location="Paris")
        db.add_all([first, second])
        db.commit()

    async def fake_logs(base_url, **_kwargs):
        if "west" in base_url:
            raise RemoteError("West is offline")
        return {
            "items": [{"id": "log-1", "ts": 123.0, "status_code": 200}],
            "next_before_ts": None,
        }

    monkeypatch.setattr(remote_client, "logs", fake_logs)
    response = client.get("/api/logs?limit=20", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert response.json()["items"][0]["instance_name"] == "East"
    assert response.json()["errors"][0]["instance_name"] == "West"


def test_token_import_audit_never_contains_secret(authenticated, monkeypatch):
    client, headers = authenticated
    secret = "VERY_SECRET_ADOBE_TOKEN"
    with SessionLocal() as db:
        instance = Instance(name="East", base_url="https://east.example", location="Tokyo")
        db.add(instance)
        db.commit()
        instance_id = instance.id

    async def fake_request(*_args, **_kwargs):
        return RemoteResponse(200, {"status": "ok"}, {})

    monkeypatch.setattr(remote_client, "request", fake_request)
    response = client.post(
        f"/api/instances/{instance_id}/tokens",
        headers=headers,
        json={"token": secret},
    )

    assert response.status_code == 200
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(AuditEvent.action == "token.add").one()
        assert audit.detail == {"count": 1}
        assert secret not in str(audit.detail)


def test_incompatible_ops_version_blocks_management(authenticated, monkeypatch):
    client, headers = authenticated
    with SessionLocal() as db:
        instance = Instance(
            name="Legacy",
            base_url="https://legacy.example",
            ops_api_version=2,
            capabilities=["tokens"],
        )
        db.add(instance)
        db.commit()
        instance_id = instance.id

    response = client.post(
        f"/api/instances/{instance_id}/tokens",
        headers=headers,
        json={"token": "TOKEN"},
    )

    assert response.status_code == 409
    assert "Unsupported Ops API version" in response.json()["detail"]


def test_account_aggregation_sorts_redacts_and_marks_cross_instance_duplicates(
    authenticated, monkeypatch
):
    client, _headers = authenticated
    with SessionLocal() as db:
        east = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["accounts"],
        )
        west = Instance(
            name="West",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["accounts"],
        )
        db.add_all([east, west])
        db.commit()

    async def fake_accounts(base_url, low_credit_threshold):
        available = 20 if "east" in base_url else 10
        return {
            "items": [
                {
                    "id": "profile-east" if "east" in base_url else "profile-west",
                    "name": "Shared account",
                    "email": "Same@Example.com",
                    "user_id": "adobe-user-1",
                    "enabled": True,
                    "health": "low_credit",
                    "low_credit": True,
                    "credits_available": available,
                    "credits_total": 1000,
                    "credential_status": "active",
                    "cookie": "SECRET_COOKIE",
                    "value": "SECRET_TOKEN",
                }
            ],
            "summary": {
                "total": 1,
                "available": 1,
                "low_credit": 1,
                "low_credit_threshold": low_credit_threshold,
                "unexpected": "SECRET",
            },
        }

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    response = client.get("/api/accounts")

    assert response.status_code == 200
    payload = response.json()
    assert [item["credits_available"] for item in payload["accounts"]] == [10, 20]
    assert all(item["duplicate"] for item in payload["accounts"])
    assert all(item["duplicate_instances"] == ["East", "West"] for item in payload["accounts"])
    assert "SECRET" not in str(payload)
    assert payload["instance_summaries"]


def test_account_aggregation_returns_partial_for_legacy_instance(authenticated, monkeypatch):
    client, _headers = authenticated
    with SessionLocal() as db:
        current = Instance(
            name="Current",
            base_url="https://current.example",
            ops_api_version=1,
            capabilities=["accounts"],
        )
        legacy = Instance(
            name="Legacy",
            base_url="https://legacy.example",
            ops_api_version=1,
            capabilities=["tokens"],
        )
        db.add_all([current, legacy])
        db.commit()

    async def fake_accounts(_base_url, _low_credit_threshold):
        return {"items": [], "summary": {"total": 0}}

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    response = client.get("/api/accounts")

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert response.json()["errors"][0]["instance_name"] == "Legacy"


def test_integration_accounts_requires_ops_key_and_returns_all_accounts(
    client, monkeypatch
):
    with SessionLocal() as db:
        instance = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["accounts"],
        )
        db.add(instance)
        db.commit()

    async def fake_accounts(_base_url, _threshold):
        return {
            "items": [
                {
                    "id": "profile-a",
                    "email": "member@example.com",
                    "health": "healthy",
                    "credits_available": 100,
                }
            ],
            "summary": {"total": 1},
        }

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    rejected = client.get("/api/integration/accounts")
    accepted = client.get(
        "/api/integration/accounts",
        headers={"X-Adobe2API-Ops-Key": "manager-test-ops"},
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["accounts"][0]["email"] == "member@example.com"


def test_low_credit_preference_is_persisted_audited_and_repolled(
    authenticated, monkeypatch
):
    client, headers = authenticated
    polls = []

    async def fake_poll():
        polls.append(True)

    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    response = client.put(
        "/api/settings/preferences",
        headers=headers,
        json={"low_credit_threshold": 250},
    )
    settings_response = client.get("/api/settings")

    assert response.status_code == 200
    assert response.json()["low_credit_threshold"] == 250
    assert settings_response.json()["low_credit_threshold"] == 250
    assert polls == [True]
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "settings.low_credit_threshold"
        ).one()
        assert audit.detail == {"previous": 100.0, "current": 250.0}


def test_auto_replacement_settings_are_persisted_and_force_credit_refresh(
    authenticated, monkeypatch
):
    client, headers = authenticated
    polls = []

    async def fake_poll(*, force_credit_refresh=False):
        polls.append(force_credit_refresh)

    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    response = client.put(
        "/api/auto-replacements/settings",
        headers=headers,
        json={"credit_threshold": 750, "refresh_interval_minutes": 12, "enabled": False},
    )
    settings_response = client.get("/api/settings")

    assert response.status_code == 200
    assert response.json()["settings"] == {
        "credit_threshold": 750.0,
        "refresh_interval_minutes": 12,
        "enabled": False,
    }
    assert settings_response.json()["auto_replacement"] == {
        "credit_threshold": 750.0,
        "refresh_interval_minutes": 12,
        "enabled": False,
    }
    assert polls == [True]
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "settings.auto_replacement"
        ).one()
        assert audit.detail["current"] == {
            "credit_threshold": 750.0,
            "refresh_interval_minutes": 12,
            "enabled": False,
        }


def test_account_batch_actions_are_grouped_per_instance_and_audited(
    authenticated, monkeypatch
):
    client, headers = authenticated
    calls = []
    with SessionLocal() as db:
        instance = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        db.add(instance)
        db.commit()
        instance_id = instance.id

    async def fake_request(_base_url, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return RemoteResponse(200, {"status": "ok"}, {})

    monkeypatch.setattr(remote_client, "request", fake_request)
    disabled = client.put(
        f"/api/instances/{instance_id}/refresh-profiles/enabled-batch",
        headers=headers,
        json={"ids": ["profile-a", "profile-b"], "enabled": False},
    )
    deleted = client.post(
        f"/api/instances/{instance_id}/refresh-profiles/delete-batch",
        headers=headers,
        json={"ids": ["profile-a", "profile-b"]},
    )

    assert disabled.status_code == 200
    assert deleted.status_code == 200
    assert calls == [
        (
            "PUT",
            "/api/v1/refresh-profiles/enabled-batch",
            {"ids": ["profile-a", "profile-b"], "enabled": False},
        ),
        (
            "POST",
            "/api/v1/refresh-profiles/delete-batch",
            {"ids": ["profile-a", "profile-b"]},
        ),
    ]
    with SessionLocal() as db:
        audits = db.query(AuditEvent).filter(
            AuditEvent.action.in_(
                ["refresh_profile.enabled_batch", "refresh_profile.delete_batch"]
            )
        ).all()
        assert [audit.detail["count"] for audit in audits] == [2, 2]
        assert "profile-a" not in str([audit.detail for audit in audits])


def test_account_safe_replace_imports_new_cookie_then_deletes_old_profile(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        instance = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        db.add(instance)
        db.commit()
        instance_id = instance.id

    secret = "SECRET_REPLACEMENT_COOKIE"
    calls = []

    async def fake_replace(email):
        assert email == "old@example.com"
        return {
            "source_email": email,
            "replacement_email": "new@example.com",
            "cookie": secret,
        }

    async def fake_accounts(_base_url, _low_credit_threshold):
        return {
            "items": [{"id": "profile-old", "email": "", "name": "old@example.com"}],
            "summary": {"total": 1},
        }

    async def fake_request(base_url, method, path, **kwargs):
        calls.append((base_url, method, path, kwargs.get("json")))
        if path.endswith("import-cookie-batch"):
            assert kwargs["json"] == {
                "items": [
                    {
                        "cookie": {"cookie": secret},
                        "name": "new@example.com",
                    }
                ]
            }
            return RemoteResponse(
                200,
                {
                    "status": "ok",
                    "imported_count": 1,
                    "failed_count": 0,
                    "refreshed_count": 1,
                    "refresh_failed_count": 0,
                    "profiles": [{"id": "profile-new"}],
                    "failed": [],
                },
                {},
            )
        assert path.endswith("delete-batch")
        assert kwargs["json"] == {"ids": ["profile-old"]}
        return RemoteResponse(
            200,
            {
                "status": "ok",
                "deleted_count": 1,
                "missing_count": 0,
                "deleted_ids": ["profile-old"],
                "missing_ids": [],
            },
            {},
        )

    async def fake_poll():
        return None

    monkeypatch.setattr(taem_client, "replace_member", fake_replace)
    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    response = client.post(
        f"/api/instances/{instance_id}/refresh-profiles/profile-old/replace-safe",
        headers=headers,
        json={"email": "OLD@Example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "已完成移除、安全补号和 Cookie 回写",
        "source_email": "old@example.com",
        "replacement_email": "new@example.com",
        "replacement_profile_id": "profile-new",
        "imported_count": 1,
        "refresh_failed_count": 0,
        "old_profile_removed": True,
    }
    assert [call[2] for call in calls] == [
        "/api/v1/refresh-profiles/import-cookie-batch",
        "/api/v1/refresh-profiles/delete-batch",
    ]
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "refresh_profile.replace_safe"
        ).one()
        assert audit.outcome == "success"
        assert audit.detail["old_profile_removed"] is True
        assert secret not in str(audit.detail)


def test_account_safe_replace_surfaces_taem_error_without_touching_instance(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        instance = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        db.add(instance)
        db.commit()
        instance_id = instance.id

    async def fake_replace(_email):
        raise TaemError("母号尚未取得管理权限，请先登录", status_code=400)

    async def fake_accounts(_base_url, _low_credit_threshold):
        return {
            "items": [{"id": "profile-old", "email": "old@example.com"}],
            "summary": {"total": 1},
        }

    remote_calls = []

    async def fake_request(*args, **kwargs):
        remote_calls.append((args, kwargs))
        return RemoteResponse(200, {}, {})

    monkeypatch.setattr(taem_client, "replace_member", fake_replace)
    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    response = client.post(
        f"/api/instances/{instance_id}/refresh-profiles/profile-old/replace-safe",
        headers=headers,
        json={"email": "old@example.com"},
    )

    assert response.status_code == 400
    assert "母号尚未取得管理权限" in response.json()["detail"]
    assert remote_calls == []
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "refresh_profile.replace_safe"
        ).one()
        assert audit.outcome == "failed"
        assert audit.detail["failed_stage"] == "taem"


def test_account_safe_replace_keeps_old_profile_when_new_cookie_import_fails(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        instance = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        db.add(instance)
        db.commit()
        instance_id = instance.id

    async def fake_replace(_email):
        return {
            "replacement_email": "new@example.com",
            "cookie": "SECRET_REPLACEMENT_COOKIE",
        }

    async def fake_accounts(_base_url, _low_credit_threshold):
        return {
            "items": [{"id": "profile-old", "email": "old@example.com"}],
            "summary": {"total": 1},
        }

    paths = []

    async def fake_request(_base_url, _method, path, **_kwargs):
        paths.append(path)
        raise RemoteError("instance offline", status_code=502)

    monkeypatch.setattr(taem_client, "replace_member", fake_replace)
    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    response = client.post(
        f"/api/instances/{instance_id}/refresh-profiles/profile-old/replace-safe",
        headers=headers,
        json={"email": "old@example.com"},
    )

    assert response.status_code == 502
    assert "母号已完成移除和补号" in response.json()["detail"]
    assert "旧 Cookie 账号仍保留" in response.json()["detail"]
    assert paths == ["/api/v1/refresh-profiles/import-cookie-batch"]


def test_account_safe_replace_job_streams_logs_then_imports_once(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        instance = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        db.add(instance)
        db.commit()
        instance_id = instance.id

    secret = "SECRET_STREAMED_REPLACEMENT_COOKIE"
    paths = []

    async def fake_accounts(_base_url, _low_credit_threshold):
        return {"items": [{"id": "profile-old", "email": "old@example.com"}]}

    async def fake_start(email):
        assert email == "old@example.com"
        return {
            "id": 41,
            "status": "running",
            "target": 1,
            "success": 0,
            "fail": 0,
            "log_total": 1,
            "logs": ["10:00:00 已定位母号"],
        }

    async def fake_get_job(job_id, *, log_offset=0):
        assert job_id == 41
        assert log_offset == 1
        return {
            "id": 41,
            "status": "done",
            "target": 1,
            "success": 1,
            "fail": 0,
            "log_total": 3,
            "logs": ["10:00:01 已移除旧子号", "10:00:02 已安全补入新子号"],
            "result": {
                "replacement": {"email": "new@example.com", "cookie": secret}
            },
        }

    async def fake_request(_base_url, _method, path, **kwargs):
        paths.append(path)
        if path.endswith("import-cookie-batch"):
            assert kwargs["json"]["items"][0]["cookie"]["cookie"] == secret
            return RemoteResponse(
                200,
                {
                    "imported_count": 1,
                    "refresh_failed_count": 0,
                    "profiles": [{"id": "profile-new"}],
                    "failed": [],
                },
                {},
            )
        return RemoteResponse(
            200,
            {"deleted_count": 1, "deleted_ids": ["profile-old"]},
            {},
        )

    async def fake_poll():
        return None

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr(taem_client, "start_replace_member", fake_start)
    monkeypatch.setattr(taem_client, "get_job", fake_get_job)
    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)

    started = client.post(
        f"/api/instances/{instance_id}/refresh-profiles/profile-old/replace-safe/start",
        headers=headers,
        json={"email": "old@example.com"},
    )
    assert started.status_code == 200
    operation = started.json()
    assert operation["status"] == "running"
    assert operation["upstream_job_id"] == 41
    assert "已定位母号" in " ".join(operation["logs"])

    completed = client.post(
        f"/api/safe-replacements/{operation['id']}/poll", headers=headers
    )
    assert completed.status_code == 200
    payload = completed.json()
    assert payload["status"] == "done"
    assert payload["phase"] == "complete"
    assert payload["result"]["replacement_email"] == "new@example.com"
    assert payload["result"]["old_profile_removed"] is True
    assert secret not in str(payload)
    assert paths == [
        "/api/v1/refresh-profiles/import-cookie-batch",
        "/api/v1/refresh-profiles/delete-batch",
    ]

    repeated = client.post(
        f"/api/safe-replacements/{operation['id']}/poll", headers=headers
    )
    assert repeated.status_code == 200
    assert paths == [
        "/api/v1/refresh-profiles/import-cookie-batch",
        "/api/v1/refresh-profiles/delete-batch",
    ]


def test_account_safe_replace_job_can_be_cancelled(authenticated, monkeypatch):
    client, headers = authenticated
    with SessionLocal() as db:
        instance = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        db.add(instance)
        db.commit()
        instance_id = instance.id

    cancelled = []
    remote_calls = []

    async def fake_accounts(_base_url, _low_credit_threshold):
        return {"items": [{"id": "profile-old", "email": "old@example.com"}]}

    async def fake_start(_email):
        return {"id": 88, "status": "running", "target": 1, "logs": []}

    async def fake_cancel(job_id):
        cancelled.append(job_id)
        return {"success": True, "message": "已请求停止拉号"}

    async def fake_get_job(job_id, *, log_offset=0):
        assert job_id == 88
        assert log_offset == 0
        return {
            "id": 88,
            "status": "cancelled",
            "target": 1,
            "success": 0,
            "fail": 0,
            "log_total": 1,
            "logs": ["10:00:03 已停止"],
        }

    async def fake_request(*args, **kwargs):
        remote_calls.append((args, kwargs))
        return RemoteResponse(200, {}, {})

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr(taem_client, "start_replace_member", fake_start)
    monkeypatch.setattr(taem_client, "cancel_job", fake_cancel)
    monkeypatch.setattr(taem_client, "get_job", fake_get_job)

    started = client.post(
        f"/api/instances/{instance_id}/refresh-profiles/profile-old/replace-safe/start",
        headers=headers,
        json={"email": "old@example.com"},
    ).json()
    stop = client.post(
        f"/api/safe-replacements/{started['id']}/cancel", headers=headers
    )
    assert stop.status_code == 200
    assert stop.json()["cancel_requested"] is True
    assert stop.json()["can_cancel"] is False
    assert cancelled == [88]

    polled = client.post(
        f"/api/safe-replacements/{started['id']}/poll", headers=headers
    )
    assert polled.status_code == 200
    assert polled.json()["status"] == "cancelled"
    assert "已停止" in " ".join(polled.json()["logs"])
    assert remote_calls == []
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "refresh_profile.replace_safe"
        ).one()
        assert audit.outcome == "cancelled"


def test_account_move_imports_target_before_deleting_source_and_preserves_headers(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        source = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        target = Instance(
            name="West",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        db.add_all([source, target])
        db.commit()
        source_id, target_id = source.id, target.id

    calls = []
    secret = "SECRET_COOKIE_VALUE"

    async def fake_request(base_url, method, path, **kwargs):
        calls.append((base_url, method, path, kwargs.get("json")))
        if path.endswith("export-cookies"):
            return RemoteResponse(
                200,
                {
                    "status": "ok",
                    "items": [
                        {
                            "id": "profile-a",
                            "name": "Account A",
                            "cookie": secret,
                            "headers": {"x-arp-session-id": "arp-session"},
                        },
                        {
                            "id": "profile-b",
                            "name": "Account B",
                            "cookie": "sid=b",
                        },
                    ],
                },
                {},
            )
        if path.endswith("import-cookie-batch"):
            assert base_url == "https://west.example"
            assert kwargs["json"]["items"] == [
                {
                    "cookie": {
                        "cookie": secret,
                        "headers": {"x-arp-session-id": "arp-session"},
                    },
                    "name": "Account A",
                },
                {"cookie": {"cookie": "sid=b"}, "name": "Account B"},
            ]
            return RemoteResponse(
                200,
                {
                    "status": "ok",
                    "imported_count": 2,
                    "failed_count": 0,
                    "refresh_failed_count": 1,
                    "profiles": [{"id": "target-a"}, {"id": "target-b"}],
                    "failed": [],
                },
                {},
            )
        assert base_url == "https://east.example"
        assert path.endswith("delete-batch")
        assert kwargs["json"] == {"ids": ["profile-a", "profile-b"]}
        return RemoteResponse(
            200,
            {
                "status": "ok",
                "deleted_count": 2,
                "missing_count": 0,
                "deleted_ids": ["profile-a", "profile-b"],
                "missing_ids": [],
            },
            {},
        )

    polls = []

    async def fake_poll():
        polls.append(True)

    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    response = client.post(
        f"/api/instances/{source_id}/refresh-profiles/move",
        headers=headers,
        json={"ids": ["profile-a", "profile-b"], "target_instance_id": target_id},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "source": {"id": source_id, "name": "East"},
        "target": {"id": target_id, "name": "West"},
        "requested_count": 2,
        "exported_count": 2,
        "imported_count": 2,
        "moved_count": 2,
        "retained_count": 0,
        "export_missing_count": 0,
        "import_failed_count": 0,
        "refresh_failed_count": 1,
        "cleanup_failed_count": 0,
        "source_state_unknown_count": 0,
    }
    assert [call[2] for call in calls] == [
        "/api/v1/refresh-profiles/export-cookies",
        "/api/v1/refresh-profiles/import-cookie-batch",
        "/api/v1/refresh-profiles/delete-batch",
    ]
    assert polls == [True]
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "refresh_profile.move_batch"
        ).one()
        assert audit.outcome == "success"
        assert audit.detail["moved_count"] == 2
        assert secret not in str(audit.detail)
        assert "profile-a" not in str(audit.detail)


def test_account_move_keeps_failed_imports_at_source(authenticated, monkeypatch):
    client, headers = authenticated
    with SessionLocal() as db:
        source = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        target = Instance(
            name="West",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        db.add_all([source, target])
        db.commit()
        source_id, target_id = source.id, target.id

    delete_bodies = []

    async def fake_request(base_url, _method, path, **kwargs):
        if path.endswith("export-cookies"):
            return RemoteResponse(
                200,
                {
                    "items": [
                        {"id": "profile-a", "name": "A", "cookie": "sid=a"},
                        {"id": "profile-b", "name": "B", "cookie": "sid=b"},
                    ]
                },
                {},
            )
        if path.endswith("import-cookie-batch"):
            return RemoteResponse(
                200,
                {
                    "status": "partial",
                    "imported_count": 1,
                    "failed_count": 1,
                    "refresh_failed_count": 0,
                    "profiles": [{"id": "target-a"}],
                    "failed": [{"index": 1, "detail": "bad cookie"}],
                },
                {},
            )
        delete_bodies.append((base_url, kwargs["json"]))
        return RemoteResponse(
            200,
            {
                "status": "ok",
                "deleted_count": 1,
                "missing_count": 0,
                "deleted_ids": ["profile-a"],
            },
            {},
        )

    async def fake_poll():
        return None

    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    response = client.post(
        f"/api/instances/{source_id}/refresh-profiles/move",
        headers=headers,
        json={"ids": ["profile-a", "profile-b"], "target_instance_id": target_id},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert response.json()["moved_count"] == 1
    assert response.json()["retained_count"] == 1
    assert response.json()["import_failed_count"] == 1
    assert delete_bodies == [
        ("https://east.example", {"ids": ["profile-a"]})
    ]


def test_account_move_rolls_back_target_when_source_delete_is_partial(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        source = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        target = Instance(
            name="West",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["refresh_profiles"],
        )
        db.add_all([source, target])
        db.commit()
        source_id, target_id = source.id, target.id

    delete_calls = []

    async def fake_request(base_url, _method, path, **kwargs):
        if path.endswith("export-cookies"):
            return RemoteResponse(
                200,
                {
                    "items": [
                        {"id": "profile-a", "cookie": "sid=a"},
                        {"id": "profile-b", "cookie": "sid=b"},
                    ]
                },
                {},
            )
        if path.endswith("import-cookie-batch"):
            return RemoteResponse(
                200,
                {
                    "imported_count": 2,
                    "profiles": [{"id": "target-a"}, {"id": "target-b"}],
                    "failed": [],
                },
                {},
            )
        delete_calls.append((base_url, kwargs["json"]))
        if base_url == "https://east.example":
            return RemoteResponse(
                200,
                {"status": "partial", "deleted_count": 1, "deleted_ids": ["profile-a"]},
                {},
            )
        return RemoteResponse(
            200,
            {"status": "ok", "deleted_count": 1, "deleted_ids": ["target-b"]},
            {},
        )

    async def fake_poll():
        return None

    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    response = client.post(
        f"/api/instances/{source_id}/refresh-profiles/move",
        headers=headers,
        json={"ids": ["profile-a", "profile-b"], "target_instance_id": target_id},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert response.json()["moved_count"] == 1
    assert response.json()["retained_count"] == 1
    assert response.json()["cleanup_failed_count"] == 0
    assert delete_calls == [
        ("https://east.example", {"ids": ["profile-a", "profile-b"]}),
        ("https://west.example", {"ids": ["target-b"]}),
    ]


def test_fleet_import_fills_targets_then_distributes_remainder(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        east = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        west = Instance(
            name="West",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        db.add_all([east, west])
        db.commit()
        east_id, west_id = east.id, west.id

    async def fake_accounts(base_url, low_credit_threshold):
        total = 1 if "east" in base_url else 3
        return {
            "items": [
                {
                    "id": f"existing-{index}",
                    "credits_available": 500,
                    "health": "healthy",
                }
                for index in range(total)
            ],
            "summary": {
                "total": total,
                "low_credit_threshold": low_credit_threshold,
            },
        }

    calls = {}

    async def fake_request(base_url, method, path, **kwargs):
        assert method == "POST"
        assert path == "/api/v1/refresh-profiles/import-cookie-batch"
        items = kwargs["json"]["items"]
        calls[base_url] = items
        return RemoteResponse(
            200,
            {
                "status": "ok",
                "imported_count": len(items),
                "failed_count": 0,
                "refreshed_count": len(items),
                "refresh_failed_count": 0,
            },
            {},
        )

    polls = []

    async def fake_poll():
        polls.append(True)

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    secret = "SECRET_COOKIE_VALUE"
    response = client.post(
        "/api/fleet/accounts/import",
        headers=headers,
        json={
            "items": [
                {"name": f"Account {index}", "cookie": f"{secret}_{index}"}
                for index in range(5)
            ],
            "targets": [
                {"instance_id": east_id, "target_count": 2},
                {"instance_id": west_id, "target_count": 3},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["imported"] == 5
    assert len(calls["https://east.example"]) == 3
    assert len(calls["https://west.example"]) == 2
    assert polls == [True]
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["preferences"]["account_targets"] == {
        east_id: 2,
        west_id: 3,
    }
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "fleet.account_import"
        ).one()
        assert audit.detail["item_count"] == 5
        assert secret not in str(audit.detail)


def test_fleet_low_credit_delete_uses_strict_threshold_and_skips_unknown(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        east = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        west = Instance(
            name="West",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        db.add_all([east, west])
        db.commit()

    async def fake_accounts(base_url, _low_credit_threshold):
        if "east" in base_url:
            values = [("low", 49), ("equal", 50), ("unknown", None)]
        else:
            values = [("zero", 0), ("healthy", 500)]
        return {
            "items": [
                {
                    "id": profile_id,
                    "credits_available": credits,
                    "health": "balance_unknown" if credits is None else "healthy",
                }
                for profile_id, credits in values
            ],
            "summary": {"total": len(values)},
        }

    calls = {}

    async def fake_request(base_url, method, path, **kwargs):
        assert method == "POST"
        assert path == "/api/v1/refresh-profiles/delete-batch"
        ids = kwargs["json"]["ids"]
        calls[base_url] = ids
        return RemoteResponse(
            200,
            {
                "status": "ok",
                "deleted_count": len(ids),
                "missing_count": 0,
            },
            {},
        )

    async def fake_poll():
        return None

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    response = client.post(
        "/api/fleet/accounts/delete-low-credit",
        headers=headers,
        json={"credit_threshold": 50},
    )

    assert response.status_code == 200
    assert response.json()["matched_count"] == 2
    assert response.json()["deleted_count"] == 2
    assert calls == {
        "https://east.example": ["low"],
        "https://west.example": ["zero"],
    }
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "fleet.low_credit_delete"
        ).one()
        assert audit.detail["credit_threshold"] == 50
        assert "low" not in str(audit.detail)


def test_fleet_mutations_abort_when_any_instance_preflight_fails(
    authenticated, monkeypatch
):
    client, headers = authenticated
    with SessionLocal() as db:
        east = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        west = Instance(
            name="West",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["accounts", "refresh_profiles"],
        )
        db.add_all([east, west])
        db.commit()
        east_id, west_id = east.id, west.id

    async def fake_accounts(base_url, _low_credit_threshold):
        if "west" in base_url:
            raise RemoteError("offline")
        return {"items": [], "summary": {"total": 0}}

    calls = []

    async def fake_request(*args, **kwargs):
        calls.append((args, kwargs))
        return RemoteResponse(200, {}, {})

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    response = client.post(
        "/api/fleet/accounts/import",
        headers=headers,
        json={
            "items": [{"cookie": "a=1"}],
            "targets": [
                {"instance_id": east_id, "target_count": 1},
                {"instance_id": west_id, "target_count": 1},
            ],
        },
    )

    assert response.status_code == 502
    assert "未执行变更" in response.json()["detail"]
    assert calls == []


def test_image_queue_aggregates_instances_and_keeps_partial_errors(
    authenticated, monkeypatch
):
    client, _headers = authenticated
    with SessionLocal() as db:
        east = Instance(
            name="East",
            location="Tokyo",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["image_queue"],
        )
        west = Instance(
            name="West",
            location="Paris",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["image_queue"],
        )
        db.add_all([east, west])
        db.commit()

    async def fake_image_queue(base_url, *, limit):
        assert limit == 150
        if "west" in base_url:
            raise RemoteError("offline")
        return {
            "summary": {
                "requests": 1,
                "outputs": 2,
                "in_progress": 1,
                "queued": 0,
                "waiting_poll": 1,
                "rate_limited": 0,
                "download_retry": 0,
            },
            "items": [
                {
                    "id": "queue-1",
                    "log_id": "log-1",
                    "path": "/v1/images/generations",
                    "model": "gpt-image-2",
                    "prompt_preview": "draw",
                    "requested_count": 2,
                    "completed_count": 1,
                    "state": "WAITING_POLL",
                    "created_at": 100,
                    "elapsed_seconds": 10,
                    "authorization": "SECRET",
                    "outputs": [
                        {
                            "index": 0,
                            "state": "COMPLETED",
                            "token_id": "masked-token",
                        },
                        {
                            "index": 1,
                            "state": "WAITING_POLL",
                            "upstream_job_id": "job-1",
                        },
                    ],
                }
            ],
        }

    monkeypatch.setattr(remote_client, "image_queue", fake_image_queue)
    response = client.get("/api/image-queue?limit_per_instance=150")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["summary"] == {
        "instances": 2,
        "instances_ok": 1,
        "instances_error": 1,
        "requests": 1,
        "outputs": 2,
        "in_progress": 1,
        "queued": 0,
        "waiting_poll": 1,
        "rate_limited": 0,
        "download_retry": 0,
    }
    assert payload["items"][0]["instance_name"] == "East"
    assert "authorization" not in payload["items"][0]
    assert payload["errors"][0]["instance_name"] == "West"


def test_fleet_credit_refresh_fans_out_and_audits(authenticated, monkeypatch):
    client, headers = authenticated
    with SessionLocal() as db:
        east = Instance(
            name="East",
            base_url="https://east.example",
            ops_api_version=1,
            capabilities=["tokens"],
        )
        west = Instance(
            name="West",
            base_url="https://west.example",
            ops_api_version=1,
            capabilities=["tokens"],
        )
        db.add_all([east, west])
        db.commit()

    calls = []

    async def fake_request(base_url, method, path, **kwargs):
        calls.append((base_url, method, path, kwargs.get("json")))
        if "west" in base_url:
            raise RemoteError("offline")
        return RemoteResponse(
            200,
            {
                "status": "partial",
                "total": 3,
                "refreshed_count": 2,
                "failed_count": 1,
            },
            {},
        )

    polls = []

    async def fake_poll():
        polls.append(True)

    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr("app.api.fleet_poller.run_once", fake_poll)
    response = client.post("/api/fleet/tokens/credits-batch", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["summary"] == {
        "instances": 2,
        "succeeded_instances": 0,
        "partial_instances": 1,
        "failed_instances": 1,
        "refreshed_count": 2,
        "failed_count": 1,
    }
    assert len(calls) == 2
    assert all(call[1:] == (
        "POST",
        "/api/v1/tokens/credits/refresh-batch",
        {"ids": None},
    ) for call in calls)
    assert polls == [True]
    with SessionLocal() as db:
        audit = db.query(AuditEvent).filter(
            AuditEvent.action == "fleet.credits_refresh"
        ).one()
        assert audit.outcome == "partial"
        assert audit.detail["refreshed_count"] == 2
