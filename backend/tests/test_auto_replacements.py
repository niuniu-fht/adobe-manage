import asyncio

from app.auto_replacements import AutoReplacementOperation, AutoReplacementService
from app.remote import RemoteResponse, remote_client
from app.taem import taem_client


def test_auto_replacement_deletes_local_before_one_shot_domain_job(
    monkeypatch,
):
    service = AutoReplacementService()
    operation = AutoReplacementOperation(
        instance_id="east",
        instance_name="East",
        base_url="https://east.example",
        profile_id="profile-old",
        source_email="old@example.com",
        trigger="积分为 0",
        credits_available=0,
        health="low_credit",
    )
    calls = []
    secret = "SECRET_DOMAIN_COOKIE"

    async def fake_accounts(_base_url, _threshold):
        calls.append("accounts")
        return {
            "items": [
                {
                    "id": "profile-old",
                    "email": "old@example.com",
                    "enabled": True,
                    "health": "low_credit",
                    "credits_available": 0,
                }
            ]
        }

    async def fake_request(_base_url, _method, path, **kwargs):
        if path.endswith("delete-batch"):
            calls.append("delete")
            assert kwargs["json"] == {"ids": ["profile-old"]}
            return RemoteResponse(
                200,
                {
                    "status": "ok",
                    "deleted_count": 1,
                    "deleted_ids": ["profile-old"],
                },
                {},
            )
        calls.append("import")
        assert kwargs["json"] == {
            "items": [
                {
                    "cookie": {"cookie": secret},
                    "name": "new@code2alita.com",
                }
            ]
        }
        return RemoteResponse(
            200,
            {"status": "ok", "imported_count": 1, "refresh_failed_count": 0},
            {},
        )

    async def fake_start(email):
        calls.append("taem-start")
        assert email == "old@example.com"
        return {"id": 73, "status": "running"}

    async def fake_get_job(job_id, *, log_offset=0):
        calls.append("taem-job")
        assert job_id == 73
        assert log_offset == 0
        return {
            "id": 73,
            "status": "done",
            "log_total": 1,
            "logs": ["域名邮箱仅尝试一次"],
            "result": {
                "replacement": {
                    "email": "new@code2alita.com",
                    "cookie": secret,
                }
            },
        }

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr(taem_client, "start_replace_member_domain", fake_start)
    monkeypatch.setattr(taem_client, "get_job", fake_get_job)

    asyncio.run(service._process(operation))

    assert calls == ["accounts", "delete", "taem-start", "taem-job", "import"]
    assert operation.status == "done"
    assert operation.phase == "complete"
    assert operation.replacement_email == "new@code2alita.com"
    assert secret not in str(operation.snapshot())
    assert "new@code2alita.com" in service._zero_credit_guards()


def test_auto_replacement_latches_zero_credit_and_credential_errors_once():
    service = AutoReplacementService()

    async def observe():
        service._queue = asyncio.Queue()
        accounts = [
            {
                "id": "zero",
                "email": "zero@example.com",
                "enabled": True,
                "health": "low_credit",
                "credits_available": 0,
            },
            {
                "id": "credential",
                "email": "credential@example.com",
                "enabled": True,
                "health": "credential_error",
                "credits_available": 500,
            },
            {
                "id": "healthy",
                "email": "healthy@example.com",
                "enabled": True,
                "health": "healthy",
                "credits_available": 500,
            },
        ]
        first = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=accounts,
        )
        second = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=accounts,
        )
        return first, second, service.snapshot()

    first, second, snapshot = asyncio.run(observe())

    assert first == 2
    assert second == 0
    assert snapshot["queued"] == 2
    assert len(snapshot["operations"]) == 2
    assert {item["trigger"] for item in snapshot["operations"]} == {
        "积分为 0",
        "凭证异常",
    }


def test_new_replacement_zero_credit_guard_prevents_replacement_loop():
    service = AutoReplacementService()
    service._save_zero_credit_guards({"new@code2alita.com": 1.0})

    async def observe():
        service._queue = asyncio.Queue()
        zero = {
            "id": "new-profile",
            "email": "new@code2alita.com",
            "enabled": True,
            "health": "low_credit",
            "credits_available": 0,
        }
        guarded = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=[zero],
        )
        healthy = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=[{**zero, "health": "healthy", "credits_available": 20}],
        )
        after_release = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=[zero],
        )
        return guarded, healthy, after_release

    guarded, healthy, after_release = asyncio.run(observe())

    assert guarded == 0
    assert healthy == 0
    assert after_release == 1
