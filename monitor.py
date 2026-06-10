import os
import requests
from datetime import datetime, date
from playwright.sync_api import sync_playwright

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
DEADLINE = date(2026, 6, 13)


def send_line_message(text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "to": LINE_USER_ID,
            "messages": [{"type": "text", "text": text}],
        },
    )


def check_availability() -> list[str]:
    available_dates = []
    today = date.today()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # トップページ → 予約ページへ進む
        page.goto("https://kirbycafe-reserve.com/guest/tokyo/", wait_until="networkidle")
        reserve_link = page.locator("a[href*='/reserve/']").first
        reserve_link.click()

        # 10分制限のポップアップを閉じる
        page.wait_for_selector("button:has-text('OK')", timeout=10000)
        page.click("button:has-text('OK')")

        # 人数入力（1名）
        page.wait_for_selector("input[type='text']", timeout=10000)
        page.fill("input[type='text']", "1")
        page.keyboard.press("Enter")

        # カレンダー読み込み待ち
        page.wait_for_timeout(3000)

        # カレンダー内の日付セルをすべて取得
        # 「×」がない日付が空き
        cells = page.locator("td, [class*='day'], [class*='date']").all()
        for cell in cells:
            text = cell.inner_text().strip()
            # 日付っぽい数字で「×」がないセルを探す
            if text.isdigit() and 1 <= int(text) <= 31:
                parent_text = cell.evaluate("el => el.closest('tr, [class*=\"week\"], [class*=\"row\"]')?.innerText || ''")
                if "×" not in cell.inner_text() and "×" not in parent_text:
                    # カレンダー上部から年月を特定
                    cal_header = page.locator("[class*='header'], [class*='month'], h2, h3").first.inner_text()
                    available_dates.append(f"{cal_header} {text}日（空きあり）")

        # スクリーンショットを保存（デバッグ用）
        page.screenshot(path="/tmp/kirby_calendar.png")
        browser.close()

    return available_dates


def main():
    if date.today() > DEADLINE:
        print("監視期間終了（6月13日を過ぎました）")
        return

    try:
        available = check_availability()
        if available:
            msg = "【キャンセル空きあり🎉】\nキャンビィカフェTOKYO\n\n" + "\n".join(available) + "\n\n今すぐ予約を！\nhttps://kirbycafe-reserve.com/guest/tokyo/"
            send_line_message(msg)
            print(f"通知送信: {available}")
        else:
            print(f"空きなし ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    except Exception as e:
        print(f"エラー: {e}")
        raise


if __name__ == "__main__":
    main()
