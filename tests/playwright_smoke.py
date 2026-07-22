import json
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8000"
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def login(page):
    page.goto(BASE_URL, wait_until="networkidle")
    page.get_by_label("访问密钥").fill("ui-test-access")
    page.get_by_role("button", name="进入控制台").click()
    page.get_by_role("heading", name="运行总览").wait_for()
    page.wait_for_load_state("networkidle")


def body_fits_viewport(page) -> bool:
    return page.evaluate(
        """() => document.documentElement.scrollWidth <= window.innerWidth + 1"""
    )


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    console_errors = []
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.goto(BASE_URL, wait_until="networkidle")
    assert page.get_by_role("heading", name="运维中心").is_visible()
    page.screenshot(path=ARTIFACTS / "login-desktop.png", full_page=True)
    login(page)

    page.locator(".fleet-row").first.wait_for()
    assert page.locator(".fleet-row").count() == 3
    assert page.get_by_text("2/3 个实例在线").is_visible()
    east_row = page.locator(".fleet-row").filter(has_text="华东节点")
    assert east_row.locator(".fleet-stat").filter(has_text="今日成功").get_by_text("412", exact=True).is_visible()
    assert east_row.locator(".fleet-stat").filter(has_text="今日失败").get_by_text("15", exact=True).is_visible()
    assert east_row.locator(".fleet-stat").filter(has_text="进行中").is_visible()
    assert body_fits_viewport(page)
    page.screenshot(path=ARTIFACTS / "overview-desktop.png", full_page=True)

    page.get_by_role("link", name="实例", exact=True).click()
    page.get_by_role("heading", name="实例管理").wait_for()
    page.locator(".table-primary").first.wait_for()
    assert page.locator("tbody tr").count() == 3
    page.get_by_role("link", name="华东节点").click()
    page.get_by_role("heading", name="实例详情").wait_for()
    page.get_by_text("24 小时趋势").wait_for()
    assert page.locator("canvas").count() == 1
    assert body_fits_viewport(page)
    page.screenshot(path=ARTIFACTS / "instance-detail-desktop.png", full_page=True)

    page.get_by_role("link", name="告警", exact=True).click()
    page.get_by_role("heading", name="告警中心").wait_for()
    page.get_by_text("欧洲节点").first.wait_for()
    assert page.get_by_text("欧洲节点").first.is_visible()
    assert page.get_by_text("告警中").first.is_visible()
    page.screenshot(path=ARTIFACTS / "alerts-desktop.png", full_page=True)

    mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mobile_page = mobile.new_page()
    login(mobile_page)
    assert mobile_page.locator(".fleet-row").count() == 3
    mobile_east = mobile_page.locator(".fleet-row").filter(has_text="华东节点")
    assert mobile_east.locator(".fleet-mobile-counts span").nth(1).is_visible()
    assert mobile_east.locator(".fleet-mobile-counts span").nth(2).is_visible()
    assert body_fits_viewport(mobile_page)
    mobile_page.screenshot(path=ARTIFACTS / "overview-mobile.png", full_page=True)
    mobile_page.get_by_title("打开导航").click()
    assert mobile_page.locator(".sidebar-open").is_visible()
    mobile_page.wait_for_timeout(250)
    mobile_page.screenshot(path=ARTIFACTS / "navigation-mobile.png", full_page=True)

    result = {
        "desktop_fleet_rows": 3,
        "desktop_canvas": 1,
        "desktop_overflow": not body_fits_viewport(page),
        "mobile_overflow": not body_fits_viewport(mobile_page),
        "console_errors": console_errors,
        "screenshots": sorted(path.name for path in ARTIFACTS.glob("*.png")),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    mobile.close()
    context.close()
    browser.close()
