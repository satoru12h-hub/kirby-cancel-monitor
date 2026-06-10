import os
import requests
from datetime import date, datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
}

# 監視設定
TARGETS = [
    {
        "name": "TOKYO",
        "slug": "tokyo",
        "base_url": "https://kirbycafe-reserve.com",
        "reserve_url": "https://kirbycafe-reserve.com/guest/tokyo/reserve/",
        "booking_url": "https://kirbycafe-reserve.com/guest/tokyo/",
        "people": 1,
        "date_from": date(2026, 6, 11),
        "date_to": date(2026, 6, 13),
    },
    {
        "name": "OSAKA",
        "slug": "osaka",
        "base_url": "https://osaka.kirbycafe-reserve.com",
        "reserve_url": "https://osaka.kirbycafe-reserve.com/guest/osaka/reserve/",
        "booking_url": "https://osaka.kirbycafe-reserve.com/guest/osaka/",
        "people": 4,
        "date_from": date(2026, 6, 13),
        "date_to": date(2026, 6, 16),
    },
]


def send_line_message(text: str):
    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]},
    )
    print(f"LINE送信結果: {resp.status_code}")


def check_via_api(target: dict) -> list[str]:
    """APIを直接叩いて空き日付を取得する（高速）"""
    session = requests.Session()
    session.headers.update(HEADERS)

    session.get(target["booking_url"])
    init = session.get(f"{target['base_url']}/api/guest/reserve/init?slug={target['slug']}")
    if init.status_code != 200:
        raise ValueError(f"API init failed: {init.status_code}")

    data = init.json()
    print(f"[{target['name']}] API response keys: {list(data.keys())}")

    available = []
    for key in ("calendar", "dates", "schedule", "slots", "availability"):
        if key not in data:
            continue
        for entry in (data[key] if isinstance(data[key], list) else []):
            d_str = entry.get("date") or entry.get("day") or ""
            status = entry.get("status") or entry.get("available") or ""
            if not d_str:
                continue
            try:
                d = date.fromisoformat(d_str[:10])
            except ValueError:
                continue
            if target["date_from"] <= d <= target["date_to"] and str(status) not in ("0", "full", "×", "false", "False"):
                available.append(f"{d.month}月{d.day}日")

    return available


def check_via_browser(target: dict) -> list[str]:
    """Playwright でブラウザを操作して空き日付を取得する（確実）"""
    available = []
    date_from = target["date_from"]
    date_to = target["date_to"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()

        page.goto(target["reserve_url"], wait_until="domcontentloaded", timeout=60000)

        # ポップアップを閉じる
        try:
            page.wait_for_selector("button:has-text('OK')", timeout=15000)
            page.click("button:has-text('OK')")
            page.wait_for_timeout(1000)
        except PWTimeout:
            pass

        # 人数入力
        try:
            page.wait_for_selector("input[type='text'], input[type='number']", timeout=15000)
            inp = page.locator("input[type='text'], input[type='number']").first
            inp.fill(str(target["people"]))
            inp.press("Enter")
            page.wait_for_timeout(3000)
        except PWTimeout:
            pass

        # カレンダー読み込み待ち
        try:
            page.wait_for_selector("td, [class*='day'], [class*='date']", timeout=20000)
        except PWTimeout:
            pass

        page.wait_for_timeout(2000)

        body_text = page.inner_text("body")[:600]
        print(f"[{target['name']}] ページテキスト: {body_text}")

        # 年月テキスト取得
        try:
            month_text = page.locator("[class*='month'], h2, h3").first.inner_text()
        except Exception:
            month_text = "6月"

        # 日付セル探索
        cells = page.locator("td, [class*='day-cell'], [class*='calendar-day']").all()
        for cell in cells:
            try:
                txt = cell.inner_text().strip()
                if not txt.isdigit():
                    continue
                day_num = int(txt)
                if not (1 <= day_num <= 31):
                    continue
                if "6月" in month_text or "June" in month_text:
                    target_date = date(date_from.year, 6, day_num)
                    if not (date_from <= target_date <= date_to):
                        continue
                else:
                    continue
                cell_html = cell.evaluate("el => el.outerHTML")
                if "×" not in cell_html:
                    available.append(f"6月{day_num}日")
            except Exception:
                continue

        browser.close()

    return list(dict.fromkeys(available))


def check_target(target: dict) -> list[str]:
    today = date.today()
    if today > target["date_to"]:
        print(f"[{target['name']}] 監視期間終了")
        return []

    available = []
    try:
        available = check_via_api(target)
        print(f"[{target['name']}] API結果: {available}")
    except Exception as e:
        print(f"[{target['name']}] API方式失敗、ブラウザ方式に切替: {e}")

    if not available:
        try:
            available = check_via_browser(target)
            print(f"[{target['name']}] ブラウザ結果: {available}")
        except Exception as e:
            print(f"[{target['name']}] ブラウザ方式も失敗: {e}")

    return available


def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    notifications = []

    for target in TARGETS:
        available = check_target(target)
        if available:
            period = f"{target['date_from'].month}月{target['date_from'].day}日〜{target['date_to'].month}月{target['date_to'].day}日"
            msg = (
                f"【カービィカフェ {target['name']}】\n"
                f"キャンセル空きが出ました！🎉\n"
                f"対象期間: {period}（{target['people']}名）\n\n"
                + "\n".join(f"✅ {d}" for d in available)
                + f"\n\n今すぐ予約を！\n{target['booking_url']}"
            )
            notifications.append(msg)

    if notifications:
        send_line_message("\n\n---\n\n".join(notifications))
        print(f"LINE通知送信完了")
    else:
        print(f"空きなし ({now_str})")


if __name__ == "__main__":
    main()
