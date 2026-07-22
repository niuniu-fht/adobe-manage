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
        "requests": {"total": 20, "successful": success, "failed": failed, "error_rate": failed / 20, "duration_p50_seconds": 0.2, "duration_p95_seconds": 0.8, "in_progress": 1, "generated_images": 10, "generated_videos": 2, "today": {"total": success + failed, "successful": success, "failed": failed, "generated_images": 10, "generated_videos": 2}},
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
        "capabilities": ["snapshot", "accounts", "refresh_profiles", "config", "cursor_logs"],
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


def fulfill(route: Route, payload: object, status: int = 200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))


def install_routes(page: Page, state: dict):
    def dashboard_route(route: Route):
        threshold = state["threshold"]
        rows = json.loads(json.dumps(INSTANCES))
        for row in rows:
            row["snapshot"]["accounts"]["low_credit_threshold"] = threshold
        fulfill(route, {"instances": rows, "summary": {"total": 3, "online": 2, "offline": 1, "active_alerts": 1}, "preferences": {"low_credit_threshold": threshold}, "updated_at": NOW})

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

    page.route("**/api/dashboard", dashboard_route)
    page.route("**/api/accounts*", all_accounts)
    page.route("**/api/instances/east/accounts", instance_accounts)
    page.route("**/api/settings/preferences", preferences)
    page.route("**/api/instances/east/refresh-profiles/**", lambda route: fulfill(route, {"status": "ok"}))
    page.route("**/api/instances/east/refresh-profiles/import", import_cookie)
    page.route("**/api/instances/east/refresh-profiles/enabled-batch", batch_action)
    page.route("**/api/instances/east/refresh-profiles/delete-batch", batch_action)
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
    state = {"threshold": 100, "account_requests": 0, "threshold_requests": [], "imports": [], "batch_actions": []}
    console_errors = []
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_routes(page, state)
    page.on("dialog", lambda dialog: dialog.accept())
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    login(page)

    assert page.locator(".fleet-row").count() == 3
    assert page.get_by_text("2/3 个实例在线").is_visible()
    assert state["account_requests"] == 0
    east_row = page.locator(".fleet-item").filter(has_text="华东节点")
    assert east_row.locator(".account-count-stat small").get_by_text("低积分 3", exact=True).is_visible()
    east_row.get_by_title("展开账号").click()
    east_row.locator(".account-table tbody tr").first.wait_for()
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

    page.get_by_role("link", name="Cookie账号", exact=True).click()
    page.get_by_role("heading", name="Cookie 账号").wait_for()
    page.locator(".account-table tbody tr").first.wait_for()
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
    assert body_fits_viewport(page)
    page.screenshot(path=ARTIFACTS / "accounts-desktop.png", full_page=True)

    page.goto(f"{BASE_URL}/instances/east", wait_until="networkidle")
    page.get_by_text("24 小时趋势").wait_for()
    assert page.locator("canvas").count() == 1
    page.screenshot(path=ARTIFACTS / "instance-detail-desktop.png", full_page=True)

    mobile_state = {"threshold": 100, "account_requests": 0, "threshold_requests": [], "imports": [], "batch_actions": []}
    mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mobile_page = mobile.new_page()
    install_routes(mobile_page, mobile_state)
    mobile_page.on("dialog", lambda dialog: dialog.accept())
    login(mobile_page)
    mobile_east = mobile_page.locator(".fleet-item").filter(has_text="华东节点")
    assert mobile_state["account_requests"] == 0
    mobile_east.get_by_title("展开账号").click()
    mobile_east.locator(".account-table tbody tr").first.wait_for()
    mobile_drawer = mobile_east.locator(".account-drawer-scroll").evaluate("el => ({clientHeight: el.clientHeight, scrollHeight: el.scrollHeight})")
    assert mobile_state["account_requests"] == 1
    assert mobile_drawer["clientHeight"] <= 220
    assert mobile_drawer["scrollHeight"] > mobile_drawer["clientHeight"]
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "overview-mobile-expanded.png", full_page=True)
    mobile_page.get_by_title("打开导航").click()
    assert mobile_page.locator(".sidebar-open").is_visible()
    mobile_page.screenshot(path=ARTIFACTS / "navigation-mobile.png", full_page=True)

    result = {
        "initial_account_requests": 0,
        "expanded_account_requests": 1,
        "desktop_drawer": drawer_size,
        "mobile_drawer": mobile_drawer,
        "desktop_overflow": not body_fits_viewport(page),
        "mobile_overflow": not body_fits_viewport(mobile_page),
        "console_errors": console_errors,
        "imports": len(state["imports"]),
        "batch_actions": len(state["batch_actions"]),
        "screenshots": sorted(path.name for path in ARTIFACTS.glob("*.png")),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    mobile.close()
    context.close()
    browser.close()
