import os
import re
import requests
from datetime import date, datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
}

TARGETS = [
    {
        "name": "TOKYO",
        "reserve_url": "https://kirbycafe-reserve.com/guest/tokyo/reserve/",
        "booking_url": "https://kirbycafe-reserve.com/guest/tokyo/",
        "people": 2,
        "date_from": date(2026, 6, 13),
        "date_to":   date(2026, 6, 30),
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


def check_via_browser(target: dict) -> list[str]:
    date_from = target["date_from"]
    date_to   = target["date_to"]
    available_slots = []   # "6月12日 10:15" のような文字列

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        page.goto(target["reserve_url"], wait_until="domcontentloaded", timeout=60000)

        # ポップアップを閉じる（OKボタン）
        try:
            page.wait_for_selector("button", timeout=10000)
            btns = page.locator("button").all()
            for btn in btns:
                if btn.inner_text().strip() == "OK":
                    btn.click()
                    break
            page.wait_for_timeout(1000)
        except PWTimeout:
            pass

        # ページ全体が安定するまで待つ
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PWTimeout:
            pass
        page.wait_for_timeout(1500)

        print(f"[{target['name']}] ページ本文: {page.inner_text('body')[:300]}")

        # 人数を選択してカレンダーを表示させる
        people_str = str(target["people"])
        selected = False

        # ① native <select> の場合
        try:
            sel = page.locator("select").first
            if sel.count() > 0:
                sel.select_option(people_str)
                page.wait_for_timeout(1500)
                selected = True
                print(f"[{target['name']}] selectで{people_str}名を選択")
        except Exception:
            pass

        # ② text/number input の場合
        if not selected:
            try:
                inp = page.locator("input[type='text'], input[type='number']").first
                if inp.count() > 0:
                    inp.click()
                    inp.fill(people_str)
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(1500)
                    selected = True
                    print(f"[{target['name']}] inputで{people_str}名を入力")
            except Exception:
                pass

        # ③ カスタムドロップダウン（div/button）の場合
        if not selected:
            try:
                # ドロップダウン的なものをクリックして開く
                page.locator("[class*='select'], [class*='dropdown'], [class*='picker']").first.click()
                page.wait_for_timeout(500)
                # 値に一致する選択肢をクリック
                page.locator(f"[role='option']:has-text('{people_str}'), li:has-text('{people_str}')").first.click()
                page.wait_for_timeout(1500)
                selected = True
                print(f"[{target['name']}] カスタムdropdownで{people_str}名を選択")
            except Exception:
                pass

        page.screenshot(path="/tmp/kirby_debug.png", full_page=True)

        # カレンダー（テーブル）が読み込まれるまで待つ
        try:
            page.wait_for_selector("table", timeout=20000)
        except PWTimeout:
            print(f"[{target['name']}] テーブルが見つかりません（人数選択: {selected}）")
            browser.close()
            return []

        page.wait_for_timeout(1500)

        # 対象期間の月ごとにチェック
        checked_months = set()
        for check_date in _date_range(date_from, date_to):
            ym = (check_date.year, check_date.month)
            if ym in checked_months:
                continue
            checked_months.add(ym)

            # 現在表示されている年月を取得
            for _ in range(6):  # 最大6ヶ月分ナビゲート
                month_el = page.locator("body").inner_text()
                m = re.search(r'(\d{4})年(\d{1,2})月', month_el)
                if m:
                    cur_year, cur_month = int(m.group(1)), int(m.group(2))
                    if (cur_year, cur_month) == ym:
                        break
                    elif (cur_year * 12 + cur_month) > (ym[0] * 12 + ym[1]):
                        # 表示が未来すぎる → 前月ボタン
                        btns = page.locator("button").all()
                        for btn in btns:
                            if "chevron_left" in btn.inner_text():
                                btn.click()
                                page.wait_for_timeout(800)
                                break
                    else:
                        # 表示が過去すぎる → 次月ボタン
                        btns = page.locator("button").all()
                        for btn in btns:
                            if "chevron_right" in btn.inner_text():
                                btn.click()
                                page.wait_for_timeout(800)
                                break
                else:
                    break

            # スキャン前に表示中の年月を検証（違う月をスキャンしない）
            shown = re.search(r'(\d{4})年(\d{1,2})月', page.locator("body").inner_text())
            if not shown or (int(shown.group(1)), int(shown.group(2))) != ym:
                print(f"[{target['name']}] 警告: {ym[0]}年{ym[1]}月に移動できず（表示中: {shown.group(0) if shown else '不明'}）スキップ")
                continue
            print(f"[{target['name']}] {shown.group(0)}のカレンダーをスキャン中")

            # テーブルヘッダーから 列インデックス→日付 のマッピングを作成
            headers = page.locator("table th").all_inner_texts()
            col_to_day = {}
            for i, h in enumerate(headers):
                dm = re.match(r'(\d+)', h.strip())
                if dm:
                    col_to_day[i - 1] = int(dm.group(1))  # th[0]は空なので-1

            # 各行を走査して ○ セルを探す
            rows = page.locator("table tr").all()
            for row in rows:
                # 行ラベル（時間帯）を取得
                time_label = ""
                th_els = row.locator("th").all()
                if th_els:
                    time_label = th_els[0].inner_text().strip()

                cells = row.locator("td").all()
                for col_idx, cell in enumerate(cells):
                    txt = cell.inner_text().strip()
                    if txt != "○":
                        continue
                    day_num = col_to_day.get(col_idx)
                    if day_num is None:
                        continue
                    try:
                        slot_date = date(ym[0], ym[1], day_num)
                    except ValueError:
                        continue
                    if date_from <= slot_date <= date_to:
                        slot_str = f"{ym[1]}月{day_num}日 {time_label}".strip()
                        available_slots.append(slot_str)

        browser.close()

    # 重複除去・ソート
    return sorted(set(available_slots))


def _date_range(d_from: date, d_to: date):
    """date_from から date_to まで月単位のリストを返す"""
    months = []
    y, m = d_from.year, d_from.month
    while (y, m) <= (d_to.year, d_to.month):
        months.append(date(y, m, 1))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def check_target(target: dict) -> list[str]:
    today = date.today()
    if today > target["date_to"]:
        print(f"[{target['name']}] 監視期間終了")
        return []

    try:
        result = check_via_browser(target)
        print(f"[{target['name']}] 結果: {result}")
        return result
    except Exception as e:
        print(f"[{target['name']}] エラー: {e}")
        return []


SEEN_FILE = "/tmp/kirby_seen_slots.txt"


def load_seen() -> set:
    try:
        with open(SEEN_FILE) as f:
            return set(f.read().splitlines())
    except FileNotFoundError:
        return set()


def save_seen(slots: set):
    with open(SEEN_FILE, "w") as f:
        f.write("\n".join(sorted(slots)))


def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    notifications = []
    all_current_slots = set()
    seen = load_seen()

    for target in TARGETS:
        available = check_target(target)
        key_prefix = target["name"]
        current_keys = {f"{key_prefix}:{s}" for s in available}
        all_current_slots |= current_keys

        # 前回から新しく増えた空き枠だけ通知
        new_slots = [s for s in available if f"{key_prefix}:{s}" not in seen]

        if new_slots:
            period = f"{target['date_from'].month}月{target['date_from'].day}日〜{target['date_to'].month}月{target['date_to'].day}日"
            msg = (
                f"【カービィカフェ {target['name']}】\n"
                f"キャンセル空きが出ました！🎉\n"
                f"対象期間: {period}（{target['people']}名）\n\n"
                + "\n".join(f"✅ {s}" for s in new_slots[:10])
                + f"\n\n今すぐ予約を！\n{target['booking_url']}"
            )
            notifications.append(msg)
            print(f"[{target['name']}] 新着空き: {new_slots}")
        else:
            print(f"[{target['name']}] 新しい空きなし（既通知: {len(seen & current_keys)}件）")

    # 今回の全空き枠を保存（次回との比較用）
    save_seen(all_current_slots)

    if notifications:
        send_line_message("\n\n---\n\n".join(notifications))
        print("LINE通知送信完了")
    else:
        print(f"変化なし ({now_str})")


if __name__ == "__main__":
    main()
