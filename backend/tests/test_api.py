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
