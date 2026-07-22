from app.database import SessionLocal
from app.models import AuditEvent, Instance
from app.remote import RemoteError, RemoteResponse, remote_client


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
