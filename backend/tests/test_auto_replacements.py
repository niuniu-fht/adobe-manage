import asyncio

from app.auto_replacements import AutoReplacementOperation, AutoReplacementService
from app.polling import FleetPoller
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
        trigger="积分低于阈值 100 (当前 25)",
        credits_available=25,
        health="low_credit",
        credit_threshold=100,
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
                    "credits_available": 25,
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


def test_scheduled_credit_refresh_obeys_interval_and_force(monkeypatch):
    poller = FleetPoller()
    calls = []

    async def fake_request(base_url, method, path, **kwargs):
        calls.append((base_url, method, path, kwargs["json"]))

    monkeypatch.setattr(remote_client, "request", fake_request)

    async def refresh():
        targets = [("east", "East", "https://east.example")]
        await poller._refresh_credits_if_due(targets, 10, force=False)
        await poller._refresh_credits_if_due(targets, 10, force=False)
        await poller._refresh_credits_if_due(targets, 10, force=True)

    asyncio.run(refresh())

    assert calls == [
        (
            "https://east.example",
            "POST",
            "/api/v1/tokens/credits/refresh-batch",
            {"ids": None},
        ),
        (
            "https://east.example",
            "POST",
            "/api/v1/tokens/credits/refresh-batch",
            {"ids": None},
        ),
    ]


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


def test_auto_replacement_queues_positive_credit_below_configured_threshold():
    service = AutoReplacementService()

    async def observe():
        service._queue = asyncio.Queue()
        queued = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            credit_threshold=100,
            accounts=[
                {
                    "id": "low-positive",
                    "email": "low@example.com",
                    "enabled": True,
                    "health": "healthy",
                    "credits_available": 25,
                }
            ],
        )
        return queued, service.snapshot()

    queued, snapshot = asyncio.run(observe())

    assert queued == 1
    assert snapshot["operations"][0]["credit_threshold"] == 100
    assert snapshot["operations"][0]["trigger"] == "积分低于阈值 100 (当前 25)"


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


def test_new_replacement_guard_waits_until_configured_threshold_is_reached():
    service = AutoReplacementService()
    service._save_zero_credit_guards({"new@code2alita.com": 1.0})

    async def observe():
        service._queue = asyncio.Queue()
        low = {
            "id": "new-profile",
            "email": "new@code2alita.com",
            "enabled": True,
            "health": "healthy",
            "credits_available": 20,
        }
        guarded = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=[low],
            credit_threshold=100,
        )
        reached = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=[{**low, "credits_available": 100}],
            credit_threshold=100,
        )
        after_release = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=[low],
            credit_threshold=100,
        )
        return guarded, reached, after_release

    guarded, reached, after_release = asyncio.run(observe())

    assert guarded == 0
    assert reached == 0
    assert after_release == 1



def test_auto_replacement_remove_only_when_refill_switch_is_closed(monkeypatch):
    service = AutoReplacementService()
    operation = AutoReplacementOperation(
        instance_id="east",
        instance_name="East",
        base_url="https://east.example",
        profile_id="profile-old",
        source_email="old@example.com",
        trigger="forbidden",
        credits_available=None,
        health="healthy",
        credit_threshold=0,
    )
    calls = []

    async def fake_accounts(_base_url, _threshold):
        calls.append("accounts")
        return {
            "items": [
                {
                    "id": "profile-old",
                    "email": "old@example.com",
                    "enabled": True,
                    "health": "healthy",
                    "last_error": "Use /v4/accounts when Arkose captcha is enabled forbidden",
                }
            ]
        }

    async def fake_request(_base_url, _method, path, **kwargs):
        calls.append(path)
        assert path.endswith("delete-batch")
        assert kwargs["json"] == {"ids": ["profile-old"]}
        return RemoteResponse(
            200,
            {"status": "ok", "deleted_count": 1, "deleted_ids": ["profile-old"]},
            {},
        )

    async def fake_remove_only(email):
        calls.append("taem-remove-only")
        assert email == "old@example.com"
        return {"id": 88, "status": "running"}

    async def fake_domain(_email):
        raise AssertionError("domain replacement should be skipped")

    async def fake_get_job(job_id, *, log_offset=0):
        calls.append("taem-job")
        assert job_id == 88
        return {
            "id": 88,
            "status": "done",
            "log_total": 1,
            "logs": ["仅移除完成"],
            "result": {"removed_only": True},
        }

    monkeypatch.setattr(remote_client, "accounts", fake_accounts)
    monkeypatch.setattr(remote_client, "request", fake_request)
    monkeypatch.setattr(taem_client, "start_remove_member_only", fake_remove_only)
    monkeypatch.setattr(taem_client, "start_replace_member_domain", fake_domain)
    monkeypatch.setattr(taem_client, "get_job", fake_get_job)
    monkeypatch.setattr(
        "app.auto_replacements.get_auto_replacement_settings",
        lambda _db: {"credit_threshold": 0.0, "refresh_interval_minutes": 5, "enabled": False},
    )

    asyncio.run(service._process(operation))

    assert calls == ["accounts", "/api/v1/refresh-profiles/delete-batch", "taem-remove-only", "taem-job"]
    assert operation.status == "done"
    assert operation.phase == "complete"
    assert operation.remove_only is True
    assert operation.replacement_email == ""
    assert "跳过后续自动补号" in "\n".join(operation.logs)


def test_auto_replacement_queues_arkose_forbidden_and_quota_errors():
    service = AutoReplacementService()

    async def observe():
        service._queue = asyncio.Queue()
        queued = await service.observe_instance(
            instance_id="east",
            instance_name="East",
            base_url="https://east.example",
            accounts=[
                {
                    "id": "arkose",
                    "email": "arkose@example.com",
                    "enabled": True,
                    "health": "healthy",
                    "credits_available": 500,
                    "last_error": "Use /v4/accounts when Arkose captcha is enabled forbidden",
                },
                {
                    "id": "quota",
                    "email": "quota@example.com",
                    "enabled": True,
                    "health": "healthy",
                    "credits_available": 500,
                    "last_error": "额度接口返回 403: Token not allowed in the current context",
                },
            ],
        )
        return queued, service.snapshot()

    queued, snapshot = asyncio.run(observe())

    assert queued == 2
    assert {item["trigger"] for item in snapshot["operations"]} == {
        "Arkose captcha / forbidden",
        "额度异常",
    }
