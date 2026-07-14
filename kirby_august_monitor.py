"""カービィカフェTOKYOの2026年8月を2名で監視し、新しい空きをLINE通知する。"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import date, datetime

import requests
from playwright.sync_api import Page, sync_playwright


CALENDAR_URL = "https://kirbycafe-reserve.com/user/auth/calendar"
LOGIN_URL = "https://kirbycafe-reserve.com/user/auth/login"
STORE = "TOKYO"
PEOPLE = 2
TARGET_YEAR = 2026
TARGET_MONTH = 8
TARGET_YYYYMM = f"{TARGET_YEAR:04d}-{TARGET_MONTH:02d}"
TARGET_MONTH_LABEL = f"{TARGET_YEAR}年{TARGET_MONTH:02d}月"

ROOT = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(ROOT, "notified_slots_kirby_august.txt")
HEALTH_FILE = os.path.join(ROOT, "kirby_august_health.txt")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
)


def send_line_message(text: str) -> None:
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    user_id = os.environ["LINE_USER_ID"]
    response = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"to": user_id, "messages": [{"type": "text", "text": text}]},
        timeout=20,
    )
    print(f"LINE送信結果: {response.status_code} {response.text[:200]}")
    response.raise_for_status()


def _shown_yyyymm(page: Page) -> tuple[int, int] | None:
    label = page.locator(".current_month").inner_text().strip()
    match = re.search(r"(\d{4})年(\d{1,2})月", label)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _wait_for_calendar(page: Page) -> None:
    page.locator(".calendar-area table").wait_for(state="visible", timeout=60_000)
    page.wait_for_function(
        """() => {
            const table = document.querySelector('.calendar-area table');
            return table && table.querySelectorAll('tbody tr').length > 0;
        }""",
        timeout=60_000,
    )


def _move_to_target_month(page: Page) -> None:
    target_index = TARGET_YEAR * 12 + TARGET_MONTH
    for _ in range(14):
        shown = _shown_yyyymm(page)
        if shown is None:
            raise RuntimeError("表示中の年月を読み取れません")
        shown_index = shown[0] * 12 + shown[1]
        if shown_index == target_index:
            return

        if shown_index < target_index:
            page.locator(".calendar-month-area .next_month button").click()
        else:
            page.locator(".calendar-month-area .before_month button").click()

        page.wait_for_timeout(500)
        page.locator(".calendar-area table").wait_for(state="visible", timeout=60_000)

    raise RuntimeError(f"{TARGET_MONTH_LABEL}へ移動できません")


def _scan_visible_calendar(page: Page) -> list[str]:
    shown = _shown_yyyymm(page)
    if shown != (TARGET_YEAR, TARGET_MONTH):
        raise RuntimeError(f"対象月が不一致です: 表示={shown} / 対象={(TARGET_YEAR, TARGET_MONTH)}")

    table = page.locator(".calendar-area table")
    headers = table.locator("thead th").all_inner_texts()
    column_days: dict[int, int] = {}
    for header_index, text in enumerate(headers):
        match = re.match(r"\s*(\d{1,2})", text)
        if match:
            # tbodyも左端の時間列をtd[0]として持つため、列番号は同じ。
            column_days[header_index] = int(match.group(1))

    if len(column_days) < 28:
        raise RuntimeError(f"日付ヘッダーが不足しています: {len(column_days)}列")

    rows = table.locator("tbody tr")
    if rows.count() == 0:
        raise RuntimeError("時間帯の行がありません")

    slots: list[str] = []
    symbol_counts = {"○": 0, "×": 0, "-": 0, "blank": 0, "other": 0}
    for row_index in range(rows.count()):
        row = rows.nth(row_index)
        cells = row.locator("td")
        if cells.count() != len(column_days) + 1:
            raise RuntimeError(
                f"カレンダーの列数が不正です: {cells.count()}列 / 日付={len(column_days)}列"
            )
        time_label = cells.nth(0).inner_text().strip()
        if not re.fullmatch(r"\d{1,2}:\d{2}", time_label):
            raise RuntimeError(f"時間帯を読み取れません: {time_label!r}")

        for column_index in range(1, cells.count()):
            symbol = cells.nth(column_index).inner_text().strip()
            if symbol in symbol_counts:
                symbol_counts[symbol] += 1
            elif symbol == "":
                symbol_counts["blank"] += 1
            else:
                symbol_counts["other"] += 1

            # 空文字・「-」・「×」は拾わない。空きは厳密に「○」だけ。
            if symbol != "○":
                continue
            day = column_days.get(column_index)
            if day is None:
                raise RuntimeError(f"空きセルの日付列を特定できません: {column_index}")
            slots.append(f"{TARGET_MONTH}月{day}日 {time_label}")

    print(f"セル内訳: {symbol_counts}")
    return sorted(set(slots), key=_slot_sort_key)


def _slot_sort_key(slot: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)月(\d+)日 (\d{1,2}):(\d{2})", slot)
    if not match:
        return 99, 99, 9999
    return int(match.group(1)), int(match.group(2)), int(match.group(3)) * 60 + int(match.group(4))


def check() -> tuple[list[str], bool]:
    """空き枠と自己点検結果を返す。予約ボタンは一切クリックしない。"""
    if date.today() > date(TARGET_YEAR, TARGET_MONTH, 31):
        print("監視対象月が終了しています")
        return [], True

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(CALENDAR_URL, wait_until="domcontentloaded", timeout=60_000)

            page.locator("#NumberOfCustomers").wait_for(state="visible", timeout=30_000)
            page.locator("#StoreSelection").wait_for(state="visible", timeout=30_000)

            # 店舗変更時に現在の人数でデータ取得されるため、人数→店舗の順に選ぶ。
            page.locator("#NumberOfCustomers").select_option(str(PEOPLE))
            page.locator("#StoreSelection").select_option(STORE)
            print(f"選択条件: 店舗={STORE} / 人数={PEOPLE}名")

            page.locator(".current_month").wait_for(state="visible", timeout=60_000)
            _wait_for_calendar(page)
            _move_to_target_month(page)
            _wait_for_calendar(page)
            page.screenshot(path="/tmp/kirby_august_debug.png", full_page=True)

            slots = _scan_visible_calendar(page)
            print(f"{TARGET_MONTH_LABEL}をスキャン: {len(slots)}件 {slots}")
            browser.close()
            return slots, True
    except Exception as error:
        print(f"監視エラー: {type(error).__name__}: {error}")
        return [], False


def _git(*args: str) -> bool:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=ROOT, check=False
        )
        if result.returncode != 0:
            print(f"git {args[0]} 失敗: {result.stderr[:300]}")
        return result.returncode == 0
    except Exception as error:
        print(f"git {args[0]} 失敗: {error}")
        return False


def _read_lines(path: str) -> set[str]:
    try:
        with open(path, encoding="utf-8") as file:
            return {line for line in file.read().splitlines() if line.strip()}
    except FileNotFoundError:
        return set()


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as file:
            return file.read().strip()
    except FileNotFoundError:
        return ""


def _commit_state(paths: list[str], message: str) -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    _git("config", "user.name", "kirby-august-bot")
    _git("config", "user.email", "bot@users.noreply.github.com")
    _git("add", *paths)
    if _git("commit", "-m", message):
        _git("pull", "--rebase", "--autostash")
        _git("push")


def load_seen() -> set[str]:
    if os.environ.get("GITHUB_ACTIONS"):
        _git("pull", "--rebase", "--autostash")
    return _read_lines(SEEN_FILE)


def save_seen(seen: set[str]) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(sorted(seen)))
        if seen:
            file.write("\n")
    _commit_state([os.path.basename(SEEN_FILE)], "カービィ8月通知済み枠を更新")


def report_health(healthy: bool) -> None:
    today = date.today().isoformat()
    previous = _read_text(HEALTH_FILE)
    if not healthy:
        if previous == f"ng:{today}":
            print("異常継続中（本日アラート済み）")
            return
        send_line_message(
            "⚠️【カービィカフェ 8月監視】\n"
            "公開カレンダーを正常に読み取れませんでした。\n"
            "サイト変更や一時的な通信障害の可能性があります。自動で再試行します。"
        )
        state = f"ng:{today}"
    else:
        if previous.startswith("ng:"):
            send_line_message("✅【カービィカフェ 8月監視】読み取りが復旧しました。監視を継続します。")
        state = "ok"

    if state != previous:
        with open(HEALTH_FILE, "w", encoding="utf-8") as file:
            file.write(state + "\n")
        _commit_state([os.path.basename(HEALTH_FILE)], "カービィ8月自己点検の状態を更新")


def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    seen = load_seen()
    available, healthy = check()
    report_health(healthy)
    if not healthy:
        return

    prefix = f"{STORE}:{PEOPLE}:2026-08"
    new_slots = [slot for slot in available if f"{prefix}:{slot}" not in seen]
    if not new_slots:
        already_seen = len([slot for slot in available if f"{prefix}:{slot}" in seen])
        print(f"新しい空きなし ({now}) / 現在の空き={len(available)} / 既通知={already_seen}")
        return

    message = (
        "【カービィカフェ TOKYO・8月】\n"
        f"{PEOPLE}名で予約できる空きが出ました！🎉\n\n"
        + "\n".join(f"✅ {slot}" for slot in new_slots[:20])
        + f"\n\nログインして予約してください。\n{LOGIN_URL}"
    )
    send_line_message(message)
    save_seen(seen | {f"{prefix}:{slot}" for slot in new_slots})
    print(f"LINE通知送信完了: {new_slots}")


if __name__ == "__main__":
    main()
