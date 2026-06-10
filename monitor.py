import os
import requests
from datetime import date
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
DEADLINE = date(2026, 6, 13)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
}


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


def check_via_api() -> list[str]:
    """APIを直接叩いて空き日付を取得する（高速）"""
    session = requests.Session()
    session.headers.update(HEADERS)

    # セッション初期化
    session.get("https://kirbycafe-reserve.com/guest/tokyo/")
    init = session.get("https://kirbycafe-reserve.com/api/guest/reserve/init?slug=tokyo")
    if init.status_code != 200:
        raise ValueError(f"API init failed: {init.status_code}")

    data = init.json()
    print(f"API response keys: {list(data.keys())}")

    # 空き日付を抽出（構造に応じて調整）
    available = []
    today = date.today()

    # カレンダーデータを探す
    for key in ("calendar", "dates", "schedule", "slots", "availability"):
        if key in data:
            entries = data[key]
            for entry in (entries if isinstance(entries, list) else []):
                d_str = entry.get("date") or entry.get("day") or ""
                status = entry.get("status") or entry.get("available") or ""
                if not d_str:
                    continue
                try:
                    d = date.fromisoformat(d_str[:10])
                except ValueError:
                    continue
                if today <= d <= DEADLINE and str(status) not in ("0", "full", "×", "false", "False"):
                    available.append(f"{d.month}月{d.day}日")

    return available


def check_via_browser() -> list[str]:
    """Playwright でブラウザを操作して空き日付を取得する（確実）"""
    available = []
    today = date.today()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()

        # 直接予約ページへ
        page.goto(
            "https://kirbycafe-reserve.com/guest/tokyo/reserve/",
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # 10分制限ポップアップを閉じる
        try:
            page.wait_for_selector("button:has-text('OK')", timeout=15000)
            page.click("button:has-text('OK')")
            page.wait_for_timeout(1000)
        except PWTimeout:
            pass

        # 人数入力（1名）
        try:
            page.wait_for_selector("input[type='text'], input[type='number']", timeout=15000)
            inp = page.locator("input[type='text'], input[type='number']").first
            inp.fill("1")
            inp.press("Enter")
            page.wait_for_timeout(3000)
        except PWTimeout:
            pass

        # カレンダー読み込み待ち（×マークが表示されるまで）
        try:
            page.wait_for_selector("td, [class*='day'], [class*='date']", timeout=20000)
        except PWTimeout:
            pass

        page.wait_for_timeout(2000)

        # スクリーンショット（デバッグ用）
        page.screenshot(path="/tmp/kirby_calendar.png", full_page=True)
        print(f"ページテキスト（冒頭500字）: {page.inner_text('body')[:500]}")

        # カレンダー上部から年月を取得
        try:
            month_text = page.locator("[class*='month'], [class*='header'] h2, h2, h3").first.inner_text()
        except Exception:
            month_text = "？月"

        # 日付セル探索：数字のみかつ×がないセルが空き
        cells = page.locator("td, [class*='day-cell'], [class*='calendar-day']").all()
        for cell in cells:
            try:
                txt = cell.inner_text().strip()
                if not txt.isdigit():
                    continue
                day_num = int(txt)
                if not (1 <= day_num <= 31):
                    continue

                # 現在表示月が6月かどうか判定（month_textに月が含まれる前提）
                if "6月" in month_text or "June" in month_text or "6" in month_text:
                    target_date = date(today.year, 6, day_num)
                    if target_date < today or target_date > DEADLINE:
                        continue
                else:
                    continue

                # 親行・セル自体に×がないか確認
                cell_html = cell.evaluate("el => el.outerHTML")
                if "×" not in cell_html:
                    available.append(f"6月{day_num}日")
            except Exception:
                continue

        browser.close()

    return list(dict.fromkeys(available))  # 重複除去


def main():
    if date.today() > DEADLINE:
        print("監視期間終了（6月13日を過ぎました）")
        return

    available = []

    # まずAPIで試みる
    try:
        available = check_via_api()
        print(f"API結果: {available}")
    except Exception as e:
        print(f"API方式失敗、ブラウザ方式に切替: {e}")

    # APIで取れなければブラウザで確認
    if not available:
        try:
            available = check_via_browser()
            print(f"ブラウザ結果: {available}")
        except Exception as e:
            print(f"ブラウザ方式も失敗: {e}")
            raise

    if available:
        msg = (
            "【キャービィカフェTOKYO】\nキャンセル空きが出ました！🎉\n\n"
            + "\n".join(f"✅ {d}" for d in available)
            + "\n\n今すぐ予約を！\nhttps://kirbycafe-reserve.com/guest/tokyo/"
        )
        send_line_message(msg)
        print(f"LINE通知送信: {available}")
    else:
        from datetime import datetime
        print(f"空きなし ({datetime.now().strftime('%Y-%m-%d %H:%M UTC')})")


if __name__ == "__main__":
    main()
