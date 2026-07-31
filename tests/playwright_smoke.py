import json
import os
import time
from pathlib import Path

from playwright.sync_api import Page, Route, sync_playwright


BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8002")
ACCESS_KEY = os.getenv("E2E_ACCESS_KEY", "ui-test-access")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
NOW = time.time()


def account(index: int, credits: int) -> dict:
    low = credits < 100
    return {
        "id": f"profile-{index}",
        "name": f"账号 {index:02d}",
        "display_name": f"Adobe Account {index:02d}",
        "email": f"account{index:02d}@example.com",
        "user_id": f"adobe-user-{index}",
        "enabled": True,
        "health": "low_credit" if low else "healthy",
        "low_credit": low,
        "credits_available": credits,
        "credits_total": 2500,
        "credits_updated_at": NOW,
        "credential_status": "active",
        "credential_expires_at": NOW + 3600,
        "consecutive_failures": 0,
        "last_attempt_at": NOW - 60,
        "last_success_at": NOW - 60,
        "next_refresh_at": NOW + 3600,
        "last_error": "",
        "imported_at": NOW - 86400,
        "instance_id": "east",
        "instance_name": "华东节点",
        "duplicate": index == 4,
        "duplicate_instances": ["华东节点", "欧洲节点"] if index == 4 else [],
    }


ACCOUNT_ROWS = [account(index, credits) for index, credits in enumerate(
    [5, 20, 99, 100, 180, 260, 400, 600, 800, 1000, 1300, 1800], start=1
)]


def snapshot(accounts_total: int, available: int, credits: int, low: int, success: int, failed: int) -> dict:
    return {
        "ops_api_version": 1,
        "measured_at": NOW,
        "instance": {"service": "adobe2api", "version": "0.2.0", "build_sha": "abc123", "started_at": NOW - 3600, "uptime_seconds": 3600},
        "requests": {"total": 20, "successful": success, "failed": failed, "error_rate": failed / 20, "duration_p50_seconds": 0.2, "duration_p95_seconds": 0.8, "in_progress": 1, "generated_images": 10, "generated_videos": 2, "today": {"total": success + failed, "successful": success, "failed": failed, "generated_images": 10, "generated_videos": 2, "safety_review_failed": max(0, failed - 2)}},
        "tokens": {"total": accounts_total, "active": available, "status_counts": {"active": available}, "expiring_24h": 0, "credits_total": accounts_total * 2500, "credits_available": credits},
        "accounts": {"total": accounts_total, "available": available, "low_credit": low, "balance_unknown": 0, "refresh_failing": 0, "credential_error": 0, "credits_available": credits, "credits_total": accounts_total * 2500, "low_credit_threshold": 100},
        "refresh_profiles": {"total": accounts_total, "failing": 0, "consecutive_failures_max": 0},
        "storage": {"generated_usage_bytes": 1024, "generated_usage_mb": 0.1, "generated_file_count": 12},
    }


def instance(instance_id: str, name: str, location: str, state: str, credits: int, low: int, success: int, failed: int) -> dict:
    return {
        "id": instance_id,
        "name": name,
        "location": location,
        "base_url": f"https://{instance_id}.example.com",
        "enabled": True,
        "tags": ["production"],
        "state": state,
        "consecutive_failures": 0,
        "last_seen_at": NOW,
        "last_failure_at": None,
        "last_error": "" if state == "online" else "connection refused",
        "latency_seconds": 0.18,
        "ops_api_version": 1,
        "capabilities": ["snapshot", "accounts", "refresh_profiles", "config", "cursor_logs", "tokens", "image_queue"],
        "snapshot": snapshot(12, 12, credits, low, success, failed),
        "heartbeat": [{"ts": NOW - index * 3600, "availability": 1 if state == "online" else 0} for index in range(48)],
        "active_alerts": 0 if state == "online" else 1,
        "created_at": NOW - 86400,
        "updated_at": NOW,
    }


INSTANCES = [
    instance("east", "华东节点", "上海", "online", 4664, 3, 412, 15),
    instance("south", "华南节点", "深圳", "online", 7100, 0, 290, 0),
    instance("europe", "欧洲节点", "法兰克福", "offline", 2300, 2, 80, 9),
]


IMAGE_QUEUE = {
    "status": "partial",
    "summary": {
        "instances": 3,
        "instances_ok": 2,
        "instances_error": 1,
        "requests": 2,
        "outputs": 5,
        "in_progress": 4,
        "queued": 1,
        "waiting_poll": 1,
        "rate_limited": 1,
        "download_retry": 1,
    },
    "instances": [
        {"instance_id": "east", "instance_name": "华东节点", "location": "上海", "status": "ok", "requests": 2, "outputs": 5, "in_progress": 4},
        {"instance_id": "south", "instance_name": "华南节点", "location": "深圳", "status": "ok", "requests": 0, "outputs": 0, "in_progress": 0},
        {"instance_id": "europe", "instance_name": "欧洲节点", "location": "法兰克福", "status": "error", "requests": 0, "outputs": 0, "in_progress": 0},
    ],
    "items": [
        {
            "id": "request-east-1",
            "log_id": "log-image-001",
            "path": "/v1/images/generations",
            "model": "gpt-image-2",
            "prompt_preview": "现代产品摄影，白色背景，清晰展示主体",
            "requested_count": 3,
            "completed_count": 1,
            "state": "RATE_LIMITED",
            "created_at": NOW - 48,
            "elapsed_seconds": 48,
            "error": "",
            "instance_id": "east",
            "instance_name": "华东节点",
            "instance_location": "上海",
            "outputs": [
                {"index": 0, "state": "COMPLETED", "account_name": "acc***01", "token_id": "", "upstream_job_id": "job-001", "retry_count": 0, "next_run_at": None, "rate_limit_wait_seconds": 0, "download_attempt": 1, "last_error": ""},
                {"index": 1, "state": "RATE_LIMITED", "account_name": "acc***02", "token_id": "", "upstream_job_id": "job-002", "retry_count": 2, "next_run_at": NOW + 8, "rate_limit_wait_seconds": 12, "download_attempt": 0, "last_error": "Too many requests"},
                {"index": 2, "state": "WAITING_POLL", "account_name": "acc***03", "token_id": "", "upstream_job_id": "job-003", "retry_count": 0, "next_run_at": NOW + 3, "rate_limit_wait_seconds": 0, "download_attempt": 0, "last_error": ""},
            ],
        },
        {
            "id": "request-east-2",
            "log_id": "log-image-002",
            "path": "/v1/images/edits",
            "model": "gpt-image-2",
            "prompt_preview": "保持人物一致，替换为城市夜景",
            "requested_count": 2,
            "completed_count": 0,
            "state": "DOWNLOAD_RETRY",
            "created_at": NOW - 22,
            "elapsed_seconds": 22,
            "error": "",
            "instance_id": "east",
            "instance_name": "华东节点",
            "instance_location": "上海",
            "outputs": [
                {"index": 0, "state": "DOWNLOAD_RETRY", "account_name": "acc***04", "token_id": "", "upstream_job_id": "job-004", "retry_count": 1, "next_run_at": NOW + 4, "rate_limit_wait_seconds": 0, "download_attempt": 2, "last_error": "presigned URL expired"},
                {"index": 1, "state": "QUEUED", "account_name": "", "token_id": "", "upstream_job_id": "", "retry_count": 0, "next_run_at": NOW + 1, "rate_limit_wait_seconds": 0, "download_attempt": 0, "last_error": ""},
            ],
        },
    ],
    "errors": [{"instance_id": "europe", "instance_name": "欧洲节点", "detail": "connection refused"}],
    "updated_at": NOW,
}

AUTO_REPLACEMENTS = {
    "active_id": "auto-op-1",
    "active": True,
    "queued": 1,
    "operations": [
        {
            "id": "auto-op-1",
            "instance_id": "east",
            "instance_name": "华东节点",
            "profile_id": "profile-1",
            "source_email": "account01@example.com",
            "trigger": "积分为 0",
            "credits_available": 0,
            "health": "low_credit",
            "status": "running",
            "phase": "mother_replacement",
            "upstream_job_id": 73,
            "logs": [
                "10:20:01 实例本地账号已移除，开始调用母号一次性域名补号",
                "10:20:02 已从母号 Adobe 团队远端移除原子号",
                "10:20:03 等待 5 秒后开始域名补号",
            ],
            "error": "",
            "replacement_email": "",
            "created_at": NOW,
            "updated_at": NOW,
        }
    ],
}


def fulfill(route: Route, payload: object, status: int = 200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))


def install_routes(page: Page, state: dict):
    def dashboard_route(route: Route):
        threshold = state["threshold"]
        rows = json.loads(json.dumps(INSTANCES))
        for row in rows:
            row["snapshot"]["accounts"]["low_credit_threshold"] = threshold
        fulfill(route, {"instances": rows, "summary": {"total": 3, "online": 2, "offline": 1, "active_alerts": 1, "total_success": 782, "total_in_progress": 3}, "preferences": {"low_credit_threshold": threshold, "account_targets": {"east": 14, "south": 12, "europe": 12}}, "updated_at": NOW})

    def instance_accounts(route: Route):
        state["account_requests"] += 1
        fulfill(route, {"status": "ok", "low_credit_threshold": state["threshold"], "accounts": ACCOUNT_ROWS, "summary": {"total": 12, "available": 12, "low_credit": 3, "balance_unknown": 0, "refresh_failing": 0, "credential_error": 0, "credits_available": 6664, "credits_total": 30000, "low_credit_threshold": state["threshold"]}})

    def all_accounts(route: Route):
        fulfill(route, {"status": "ok", "low_credit_threshold": state["threshold"], "accounts": ACCOUNT_ROWS, "instance_summaries": {}, "errors": []})

    def preferences(route: Route):
        body = route.request.post_data_json
        state["threshold"] = float(body["low_credit_threshold"])
        state["threshold_requests"].append(body)
        fulfill(route, {"status": "ok", "low_credit_threshold": state["threshold"]})

    def import_cookie(route: Route):
        state["imports"].append(route.request.post_data_json)
        fulfill(route, {"status": "ok", "refresh_error": ""})

    def batch_action(route: Route):
        state["batch_actions"].append({
            "method": route.request.method,
            "url": route.request.url,
            "body": route.request.post_data_json,
        })
        fulfill(route, {"status": "ok"})

    def move_accounts(route: Route):
        body = route.request.post_data_json
        state["moves"].append(body)
        target = next(item for item in INSTANCES if item["id"] == body["target_instance_id"])
        count = len(body["ids"])
        fulfill(route, {
            "status": "ok",
            "source": {"id": "east", "name": "华东节点"},
            "target": {"id": target["id"], "name": target["name"]},
            "requested_count": count,
            "exported_count": count,
            "imported_count": count,
            "moved_count": count,
            "retained_count": 0,
            "export_missing_count": 0,
            "import_failed_count": 0,
            "refresh_failed_count": 0,
            "cleanup_failed_count": 0,
            "source_state_unknown_count": 0,
        })

    def safe_replace_operation(status: str = "running"):
        done = status == "done"
        cancelled = status == "cancelled"
        return {
            "id": "safe-operation-1",
            "status": status,
            "phase": "complete" if done else "pulling",
            "upstream_job_id": 27,
            "target": 1,
            "success": 1 if done else 0,
            "fail": 0,
            "logs": [
                "10:24:01 已定位母号并确认管理权限",
                "10:24:02 已移除旧子号",
                "10:24:03 正在等待安全补号",
            ] + (["10:24:04 已停止拉号，未执行 Cookie 回写"] if cancelled else []),
            "error": "拉号任务已停止" if cancelled else "",
            "result": {
                "status": "ok",
                "message": "已完成移除、安全补号和 Cookie 回写",
                "source_email": state.get("safe_replace_email", "account01@example.com"),
                "replacement_email": "replacement@example.com",
                "replacement_profile_id": "profile-new",
                "imported_count": 1,
                "refresh_failed_count": 0,
                "old_profile_removed": True,
            } if done else None,
            "created_at": NOW,
            "updated_at": NOW + state.get("safe_replace_polls", 0),
            "can_cancel": status == "running" and not state.get("safe_replace_cancelled"),
            "cancel_requested": bool(state.get("safe_replace_cancelled")),
        }

    def safe_replace_start(route: Route):
        body = route.request.post_data_json
        state["safe_replaces"].append(body)
        if state.get("safe_replace_error"):
            fulfill(route, {"detail": "母号移除并安全补号失败：母号尚未取得管理权限，请先登录"}, status=400)
            return
        state["safe_replace_email"] = body["email"]
        state["safe_replace_polls"] = 0
        state["safe_replace_cancelled"] = False
        fulfill(route, safe_replace_operation())

    def safe_replace_poll(route: Route):
        state["safe_replace_polls"] += 1
        if state.get("safe_replace_cancelled"):
            fulfill(route, safe_replace_operation("cancelled"))
        elif state.get("hold_safe_replace") or state["safe_replace_polls"] == 1:
            fulfill(route, safe_replace_operation())
        else:
            fulfill(route, safe_replace_operation("done"))

    def safe_replace_cancel(route: Route):
        state["safe_replace_cancelled"] = True
        state["safe_replace_cancels"] += 1
        fulfill(route, safe_replace_operation())

    def fleet_import(route: Route):
        body = route.request.post_data_json
        state["fleet_imports"].append(body)
        assignments = [2, 0, 0]
        fulfill(route, {
            "status": "ok",
            "total": len(body["items"]),
            "assigned": len(body["items"]),
            "imported": len(body["items"]),
            "failed": 0,
            "refreshed": len(body["items"]),
            "refresh_failed": 0,
            "instances": [
                {
                    "instance_id": item["id"],
                    "instance_name": item["name"],
                    "before_count": 12,
                    "target_count": next(target["target_count"] for target in body["targets"] if target["instance_id"] == item["id"]),
                    "deficit": 2 if item["id"] == "east" else 0,
                    "assigned_count": assignments[index],
                    "imported_count": assignments[index],
                    "failed_count": 0,
                    "refreshed_count": assignments[index],
                    "refresh_failed_count": 0,
                    "status": "ok" if assignments[index] else "skipped",
                    "error": "",
                }
                for index, item in enumerate(INSTANCES)
            ],
        })

    def fleet_delete(route: Route):
        body = route.request.post_data_json
        state["fleet_deletes"].append(body)
        matched = sum(1 for item in ACCOUNT_ROWS if item["credits_available"] < body["credit_threshold"])
        fulfill(route, {
            "status": "ok",
            "credit_threshold": body["credit_threshold"],
            "matched_count": matched,
            "deleted_count": matched,
            "missing_count": 0,
            "failed_instances": 0,
            "instances": [{
                "instance_id": "east",
                "instance_name": "华东节点",
                "matched_count": matched,
                "deleted_count": matched,
                "missing_count": 0,
                "status": "ok",
                "error": "",
            }],
        })

    def fleet_credit_refresh(route: Route):
        state["fleet_credit_refreshes"].append(route.request.post_data_json or {})
        fulfill(route, {
            "status": "partial",
            "summary": {
                "instances": 3,
                "successful_instances": 2,
                "failed_instances": 1,
                "refreshed_count": 21,
                "failed_count": 2,
            },
            "instances": [],
        })

    page.route("**/api/dashboard", dashboard_route)
    page.route("**/api/auto-replacements", lambda route: fulfill(route, AUTO_REPLACEMENTS))
    page.route("**/api/accounts*", all_accounts)
    page.route("**/api/instances/east/accounts", instance_accounts)
    page.route("**/api/settings/preferences", preferences)
    page.route("**/api/fleet/accounts/import", fleet_import)
    page.route("**/api/fleet/accounts/delete-low-credit", fleet_delete)
    page.route("**/api/fleet/tokens/credits-batch", fleet_credit_refresh)
    page.route("**/api/image-queue?*", lambda route: fulfill(route, IMAGE_QUEUE))
    page.route("**/api/instances/east/refresh-profiles/**", lambda route: fulfill(route, {"status": "ok"}))
    page.route("**/api/instances/east/refresh-profiles/import", import_cookie)
    page.route("**/api/instances/east/refresh-profiles/enabled-batch", batch_action)
    page.route("**/api/instances/east/refresh-profiles/delete-batch", batch_action)
    page.route("**/api/instances/east/refresh-profiles/move", move_accounts)
    page.route("**/api/instances/east/refresh-profiles/*/replace-safe/start", safe_replace_start)
    page.route("**/api/safe-replacements/*/poll", safe_replace_poll)
    page.route("**/api/safe-replacements/*/cancel", safe_replace_cancel)
    page.route("**/api/instances/east/tokens/credits-batch", lambda route: fulfill(route, {"status": "ok"}))
    page.route("**/api/instances/east/metrics?*", lambda route: fulfill(route, {"items": [{"ts": NOW - 3600, "latency_seconds": 0.2, "error_rate": 0.01, "active_tokens": 12}, {"ts": NOW, "latency_seconds": 0.3, "error_rate": 0.02, "active_tokens": 12}]}))
    page.route("**/api/instances/east", lambda route: fulfill(route, INSTANCES[0]))
    page.route("**/api/instances", lambda route: fulfill(route, {"instances": INSTANCES}))


def login(page: Page):
    page.goto(BASE_URL, wait_until="networkidle")
    page.get_by_label("访问密钥").fill(ACCESS_KEY)
    page.get_by_role("button", name="进入控制台").click()
    page.get_by_role("heading", name="运行总览").wait_for()
    page.wait_for_load_state("networkidle")


def body_fits_viewport(page: Page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    state = {"threshold": 100, "account_requests": 0, "threshold_requests": [], "imports": [], "batch_actions": [], "moves": [], "safe_replaces": [], "safe_replace_error": False, "safe_replace_polls": 0, "safe_replace_cancels": 0, "safe_replace_cancelled": False, "hold_safe_replace": False, "fleet_imports": [], "fleet_deletes": [], "fleet_credit_refreshes": []}
    console_errors = []
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_routes(page, state)
    page.on("dialog", lambda dialog: dialog.accept())
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    login(page)

    assert page.locator(".fleet-row").count() == 3
    assert page.get_by_text("2/3 个实例在线").is_visible()
    assert page.locator(".metric-band").get_by_text("总成功数", exact=True).is_visible()
    assert page.locator(".metric-band").get_by_text("782", exact=True).is_visible()
    assert page.locator(".metric-band").get_by_text("总进行中数", exact=True).is_visible()
    assert page.get_by_label("自动移除补号控制台").is_visible()
    assert page.get_by_label("自动移除补号控制台").get_by_text("等待 5 秒后开始域名补号", exact=False).is_visible()
    assert state["account_requests"] == 0
    assert body_fits_viewport(page)
    page.screenshot(path=ARTIFACTS / "overview-stats-desktop.png", full_page=True)

    page.get_by_role("button", name="刷新全部额度", exact=True).click()
    page.get_by_text("额度刷新完成：成功 21，失败 2，异常实例 1").wait_for()
    assert state["fleet_credit_refreshes"] == [{}]

    page.get_by_role("button", name="统一导入").click()
    fleet_import_dialog = page.get_by_role("dialog", name="统一导入 Cookie 账号")
    assert fleet_import_dialog.locator(".fleet-target-list label").count() == 3
    fleet_import_dialog.locator(".cookie-paste").fill('[{"name":"New A","cookie":"sid=a"},{"name":"New B","cookie":"sid=b"}]')
    fleet_import_dialog.get_by_text("已解析 2 个 Cookie 账号").wait_for()
    fleet_import_dialog.get_by_role("button", name="分配并导入 2 个账号").click()
    fleet_import_dialog.get_by_text("导入成功").wait_for()
    assert len(state["fleet_imports"]) == 1
    assert state["fleet_imports"][0]["targets"][0] == {"instance_id": "east", "target_count": 14}
    page.screenshot(path=ARTIFACTS / "fleet-import-desktop.png", full_page=True)
    fleet_import_dialog.get_by_role("button", name="完成").click()

    page.get_by_role("button", name="低积分清理").click()
    delete_dialog = page.get_by_role("dialog", name="清理低积分账号")
    delete_dialog.get_by_text("匹配账号").wait_for()
    delete_dialog.get_by_role("button", name="删除 3 个账号").click()
    delete_dialog.get_by_text("已删除").wait_for()
    assert state["fleet_deletes"] == [{"credit_threshold": 100}]
    page.screenshot(path=ARTIFACTS / "fleet-delete-desktop.png", full_page=True)
    delete_dialog.get_by_role("button", name="完成").click()
    east_row = page.locator(".fleet-item").filter(has_text="华东节点")
    assert east_row.locator(".account-count-stat small").get_by_text("低积分 3", exact=True).is_visible()
    assert east_row.locator(".fleet-stat").filter(has_text="审核失败").get_by_text("13", exact=True).is_visible()
    east_row.get_by_title("展开账号").click()
    east_row.get_by_text("Adobe Account 01", exact=True).wait_for()
    assert state["account_requests"] == 1
    assert east_row.locator(".account-table tbody tr").count() == 12
    assert east_row.locator(".account-table tbody tr").first.locator("td").nth(3).get_by_text("5", exact=True).is_visible()
    drawer_size = east_row.locator(".account-drawer-scroll").evaluate("el => ({clientHeight: el.clientHeight, scrollHeight: el.scrollHeight})")
    assert drawer_size["clientHeight"] <= 260
    assert drawer_size["scrollHeight"] > drawer_size["clientHeight"]

    threshold = page.locator(".threshold-control input")
    threshold.fill("150")
    page.get_by_title("保存低积分阈值").click()
    page.get_by_text("低积分阈值已保存").wait_for()
    assert state["threshold_requests"][-1] == {"low_credit_threshold": 150}

    east_row.get_by_role("button", name="导入").click()
    dialog = page.get_by_role("dialog", name="导入 Cookie 账号")
    dialog.locator(".cookie-paste").fill("Cookie: sid=one; auth=two")
    page.screenshot(path=ARTIFACTS / "cookie-import-desktop.png", full_page=True)
    dialog.get_by_role("button", name="导入 1 个账号").click()
    dialog.get_by_text("导入成功").wait_for()
    assert state["imports"][-1]["cookie"] == "sid=one; auth=two"
    dialog.get_by_role("button", name="完成").click()

    east_row.get_by_role("button", name="导入").click()
    dialog.get_by_role("button", name="文件").click()
    dialog.locator('input[type="file"]').set_input_files({"name": "account.json", "mimeType": "application/json", "buffer": b'[{"name":"sid","value":"file-cookie"}]'})
    dialog.get_by_text("已解析 1 个 Cookie 账号").wait_for()
    dialog.get_by_role("button", name="导入 1 个账号").click()
    dialog.get_by_text("导入成功").wait_for()
    assert state["imports"][-1]["cookie"] == "sid=file-cookie"
    dialog.get_by_role("button", name="完成").click()

    east_row.get_by_label("选择 Adobe Account 01").click()
    east_row.get_by_label("选择 Adobe Account 02").click()
    east_row.get_by_role("button", name="批量移动").click()
    move_dialog = page.get_by_role("dialog", name="批量移动 Cookie 账号")
    move_dialog.get_by_label("目标实例", exact=True).select_option("south")
    page.screenshot(path=ARTIFACTS / "account-move-desktop.png", full_page=True)
    move_dialog.get_by_role("button", name="移动 2 个账号").click()
    move_dialog.get_by_text("移动完成").wait_for()
    assert state["moves"] == [{"ids": ["profile-1", "profile-2"], "target_instance_id": "south"}]
    move_dialog.get_by_role("button", name="完成").click()

    page.get_by_role("link", name="Cookie账号", exact=True).click()
    page.get_by_role("heading", name="Cookie 账号").wait_for()
    page.get_by_text("Adobe Account 01", exact=True).wait_for()
    assert page.locator(".account-table tbody tr").count() == 12
    page.get_by_label("选择 Adobe Account 01").click()
    page.get_by_label("选择 Adobe Account 02").click()
    page.screenshot(path=ARTIFACTS / "accounts-batch-desktop.png", full_page=True)
    page.get_by_role("button", name="批量暂停").click()
    page.get_by_text("已批量暂停 2 个账号").wait_for()
    assert state["batch_actions"][-1]["body"] == {"ids": ["profile-1", "profile-2"], "enabled": False}
    page.get_by_label("选择 Adobe Account 01").click()
    page.get_by_label("选择 Adobe Account 02").click()
    page.get_by_role("button", name="批量删除").click()
    page.get_by_text("已批量删除 2 个账号").wait_for()
    assert state["batch_actions"][-1]["body"] == {"ids": ["profile-1", "profile-2"]}

    first_account_row = page.locator(".account-table tbody tr").first
    first_account_row.get_by_title("移除并安全补号").click()
    replace_dialog = page.get_by_role("dialog", name="移除并安全补号", exact=True)
    assert replace_dialog.get_by_text("account01@example.com", exact=True).is_visible()
    assert replace_dialog.get_by_text("华东节点", exact=True).is_visible()
    page.screenshot(path=ARTIFACTS / "account-safe-replace-confirm-desktop.png", full_page=True)
    replace_dialog.get_by_role("button", name="移除并安全补号", exact=True).click()
    replace_dialog.get_by_text("已移除旧子号", exact=False).wait_for()
    assert replace_dialog.get_by_role("button", name="停止拉号", exact=True).is_visible()
    page.screenshot(path=ARTIFACTS / "account-safe-replace-progress-desktop.png", full_page=True)
    replace_dialog.get_by_text("replacement@example.com", exact=True).wait_for()
    assert state["safe_replaces"][-1] == {"email": "account01@example.com"}
    page.screenshot(path=ARTIFACTS / "account-safe-replace-success-desktop.png", full_page=True)
    replace_dialog.get_by_role("button", name="确认").click()

    state["hold_safe_replace"] = True
    page.locator(".account-table tbody tr").nth(2).get_by_title("移除并安全补号").click()
    stopped_replace_dialog = page.get_by_role("dialog", name="移除并安全补号", exact=True)
    stopped_replace_dialog.get_by_role("button", name="移除并安全补号", exact=True).click()
    stopped_replace_dialog.get_by_text("已移除旧子号", exact=False).wait_for()
    stopped_replace_dialog.get_by_role("button", name="停止拉号", exact=True).click()
    stopped_dialog = page.get_by_role("dialog", name="拉号任务已停止")
    stopped_dialog.get_by_text("未执行 Cookie 回写", exact=False).wait_for()
    assert state["safe_replace_cancels"] == 1
    page.screenshot(path=ARTIFACTS / "account-safe-replace-stopped-desktop.png", full_page=True)
    stopped_dialog.get_by_role("button", name="确认").click()
    state["hold_safe_replace"] = False

    state["safe_replace_error"] = True
    page.locator(".account-table tbody tr").nth(1).get_by_title("移除并安全补号").click()
    failed_replace_dialog = page.get_by_role("dialog", name="移除并安全补号", exact=True)
    failed_replace_dialog.get_by_role("button", name="移除并安全补号", exact=True).click()
    error_dialog = page.get_by_role("dialog", name="移除并安全补号出现错误")
    error_dialog.get_by_text("母号尚未取得管理权限", exact=False).wait_for()
    assert error_dialog.get_by_role("button", name="确认").is_visible()
    page.screenshot(path=ARTIFACTS / "account-safe-replace-error-desktop.png", full_page=True)
    error_dialog.get_by_role("button", name="确认").click()
    state["safe_replace_error"] = False
    assert body_fits_viewport(page)
    page.screenshot(path=ARTIFACTS / "accounts-desktop.png", full_page=True)

    page.get_by_role("link", name="图片队列", exact=True).click()
    page.get_by_text("跨实例图片队列").wait_for()
    assert page.get_by_text("4", exact=True).first.is_visible()
    assert page.locator(".manager-queue-title strong").get_by_text("华东节点", exact=True).first.is_visible()
    assert page.get_by_text("429 等待", exact=True).count() >= 1
    assert page.locator(".manager-queue-request").count() == 2
    output_scroll = page.locator(".manager-queue-output-scroll").first.evaluate("el => ({clientWidth: el.clientWidth, scrollWidth: el.scrollWidth})")
    assert output_scroll["scrollWidth"] >= output_scroll["clientWidth"]
    assert body_fits_viewport(page)
    page.screenshot(path=ARTIFACTS / "image-queue-desktop.png", full_page=True)

    page.goto(f"{BASE_URL}/instances/east", wait_until="networkidle")
    page.get_by_text("24 小时趋势").wait_for()
    assert page.locator("canvas").count() == 1
    page.screenshot(path=ARTIFACTS / "instance-detail-desktop.png", full_page=True)

    mobile_state = {"threshold": 100, "account_requests": 0, "threshold_requests": [], "imports": [], "batch_actions": [], "moves": [], "safe_replaces": [], "safe_replace_error": False, "safe_replace_polls": 0, "safe_replace_cancels": 0, "safe_replace_cancelled": False, "hold_safe_replace": False, "fleet_imports": [], "fleet_deletes": [], "fleet_credit_refreshes": []}
    mobile = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
        storage_state=context.storage_state(),
    )
    mobile_page = mobile.new_page()
    install_routes(mobile_page, mobile_state)
    mobile_page.on("dialog", lambda dialog: dialog.accept())
    mobile_page.goto(BASE_URL, wait_until="networkidle")
    mobile_page.get_by_role("heading", name="运行总览").wait_for()
    assert mobile_page.locator(".metric-band").get_by_text("总成功数", exact=True).is_visible()
    assert mobile_page.get_by_label("自动移除补号控制台").is_visible()
    assert mobile_page.locator(".fleet-item").filter(has_text="华东节点").locator(".fleet-mobile-counts span").filter(has_text="审核失败").is_visible()
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "overview-stats-mobile.png", full_page=True)
    mobile_page.get_by_role("button", name="统一导入").click()
    mobile_import = mobile_page.get_by_role("dialog", name="统一导入 Cookie 账号")
    assert mobile_import.locator(".fleet-target-list label").count() == 3
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "fleet-import-mobile.png", full_page=True)
    mobile_import.get_by_title("关闭").click()
    mobile_east = mobile_page.locator(".fleet-item").filter(has_text="华东节点")
    assert mobile_state["account_requests"] == 0
    mobile_east.get_by_title("展开账号").click()
    mobile_east.get_by_text("Adobe Account 01", exact=True).wait_for()
    mobile_drawer = mobile_east.locator(".account-drawer-scroll").evaluate("el => ({clientHeight: el.clientHeight, scrollHeight: el.scrollHeight})")
    assert mobile_state["account_requests"] == 1
    assert mobile_drawer["clientHeight"] <= 220
    assert mobile_drawer["scrollHeight"] > mobile_drawer["clientHeight"]
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "overview-mobile-expanded.png", full_page=True)
    mobile_east.get_by_label("选择 Adobe Account 01").click()
    mobile_east.get_by_label("选择 Adobe Account 02").click()
    mobile_east.get_by_role("button", name="批量移动").click()
    mobile_move = mobile_page.get_by_role("dialog", name="批量移动 Cookie 账号")
    mobile_move.get_by_label("目标实例", exact=True).select_option("south")
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "account-move-mobile.png", full_page=True)
    mobile_move.get_by_title("关闭").click()
    mobile_page.get_by_title("打开导航").click()
    mobile_page.get_by_role("link", name="Cookie账号", exact=True).click()
    mobile_page.get_by_text("Adobe Account 01", exact=True).wait_for()
    mobile_page.locator(".account-table tbody tr").first.get_by_title("移除并安全补号").click()
    mobile_replace = mobile_page.get_by_role("dialog", name="移除并安全补号", exact=True)
    assert mobile_replace.get_by_role("button", name="移除并安全补号", exact=True).is_visible()
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "account-safe-replace-mobile.png", full_page=True)
    mobile_state["hold_safe_replace"] = True
    mobile_replace.get_by_role("button", name="移除并安全补号", exact=True).click()
    mobile_replace.get_by_text("已移除旧子号", exact=False).wait_for()
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "account-safe-replace-progress-mobile.png", full_page=True)
    mobile_replace.get_by_role("button", name="停止拉号", exact=True).click()
    mobile_page.get_by_role("dialog", name="拉号任务已停止").get_by_text("未执行 Cookie 回写", exact=False).wait_for()
    mobile_page.get_by_role("dialog", name="拉号任务已停止").get_by_role("button", name="确认").click()
    mobile_page.get_by_title("打开导航").click()
    assert mobile_page.locator(".sidebar-open").is_visible()
    mobile_page.screenshot(path=ARTIFACTS / "navigation-mobile.png", full_page=True)
    mobile_page.get_by_role("link", name="图片队列", exact=True).click()
    mobile_page.get_by_text("跨实例图片队列").wait_for()
    mobile_page.wait_for_timeout(350)
    assert not mobile_page.locator(".sidebar-open").is_visible()
    mobile_output_scroll = mobile_page.locator(".manager-queue-output-scroll").first.evaluate("el => ({clientWidth: el.clientWidth, scrollWidth: el.scrollWidth})")
    assert mobile_output_scroll["scrollWidth"] > mobile_output_scroll["clientWidth"]
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "image-queue-mobile.png", full_page=True)

    result = {
        "initial_account_requests": 0,
        "expanded_account_requests": 1,
        "desktop_drawer": drawer_size,
        "mobile_drawer": mobile_drawer,
        "desktop_queue_scroll": output_scroll,
        "mobile_queue_scroll": mobile_output_scroll,
        "desktop_overflow": not body_fits_viewport(page),
        "mobile_overflow": not body_fits_viewport(mobile_page),
        "console_errors": console_errors,
        "imports": len(state["imports"]),
        "batch_actions": len(state["batch_actions"]),
        "moves": len(state["moves"]),
        "safe_replaces": len(state["safe_replaces"]),
        "screenshots": sorted(path.name for path in ARTIFACTS.glob("*.png")),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    mobile.close()
    context.close()
    browser.close()
