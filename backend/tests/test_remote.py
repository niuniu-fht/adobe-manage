import asyncio
from datetime import datetime

from app.remote import AdobeInstanceClient, RemoteResponse


def test_snapshot_counts_today_safety_review_failures_across_pages(monkeypatch):
    client = AdobeInstanceClient()
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    calls = []

    async def fake_request(base_url, method, path, **kwargs):
        calls.append((base_url, method, path, kwargs.get("params")))
        if path == "/api/v1/ops/snapshot":
            return RemoteResponse(
                200,
                {
                    "measured_at": start + 3600,
                    "requests": {
                        "successful": 8,
                        "failed": 3,
                        "in_progress": 1,
                        "today": {"successful": 8, "failed": 3},
                    },
                },
                {},
            )
        before_ts = (kwargs.get("params") or {}).get("before_ts")
        if before_ts is None:
            return RemoteResponse(
                200,
                {
                    "items": [
                        {
                            "ts": start + 300,
                            "error_code": "IMAGE_UNSAFE",
                        },
                        {
                            "ts": start + 200,
                            "error": "upstream timeout",
                        },
                    ],
                    "next_before_ts": start + 200,
                },
                {},
            )
        return RemoteResponse(
            200,
            {
                "items": [
                    {
                        "ts": start + 100,
                        "upstream_error_code": "content_policy_violation",
                    },
                    {
                        "ts": start - 1,
                        "error_code": "IMAGE_UNSAFE",
                    },
                ],
                "next_before_ts": start - 1,
            },
            {},
        )

    monkeypatch.setattr(client, "request", fake_request)
    payload = asyncio.run(client.snapshot("https://east.example"))

    assert payload["requests"]["today"]["safety_review_failed"] == 2
    assert [call[2] for call in calls] == [
        "/api/v1/ops/snapshot",
        "/api/v1/ops/logs",
        "/api/v1/ops/logs",
    ]
    assert calls[1][3] == {"limit": 500, "errors_only": "true"}
    assert calls[2][3]["before_ts"] == start + 200
