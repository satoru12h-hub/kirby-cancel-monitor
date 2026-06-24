"""JAL工場見学〜SKY MUSEUM〜 の空き監視。
指定人数（既定4名）で予約可能な枠が出たらLINE通知する。
空き状況ページはパラメータ付きURLに直接アクセスするだけで取得できる
（同意画面・フォーム操作が不要）。
"""
import os
import re
import subprocess
from datetime import date, datetime

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

# ===== 監視設定 =====
PEOPLE = 4                         # 何名分の空きを探すか
COURSE_KEYWORD = "工場見学コース"   # 監視するコース名（部分一致）。""にすると全コース
MONTHS_AHEAD = 4                   # 今月から何ヶ月先まで見るか
ENTRY_URL = "https://jalfactorytour.my.salesforce-sites.com/"  # 通知に載せる予約入口
# ====================

BASE = ("https://jalfactorytour.my.salesforce-sites.com/rselectcourse"
        "?month={m}&numberOfPeople={p}&useWheelchair=%E4%B8%8D%E8%A6%81+Unnecessary&year={y}")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# 予約可能とみなす記号（人数を渡しているため、要求人数に満たない枠は「不足」になり拾わない）
AVAIL_SYMBOLS = {"○", "△"}


def send_line_message(text: str):
    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]},
    )
    print(f"LINE送信結果: {resp.status_code} {resp.text[:200]}")


def cell_bookable(alts: list[str]) -> bool:
    """セル画像のalt文字から、指定人数で予約可能かを判定。"""
    for a in alts:
        a = (a or "").strip()
        if a in AVAIL_SYMBOLS:        # ○=残り16名以上, △=残り15〜6名
            return True
        if a.isdigit():               # 残り5〜1名（要求人数を満たす場合のみ表示される）
            return True
    return False


def _months_to_scan() -> list[tuple[int, int]]:
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(MONTHS_AHEAD + 1):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _infer_year(month: int) -> int:
    """日付ラベルの月から年を推定（年またぎ対応）。"""
    today = date.today()
    if month >= today.month:
        return today.year
    return today.year + 1


def scan_month(page, y: int, m: int) -> list[tuple]:
    """1リクエスト分のページを開き、予約可能な (date, course, time) を返す。"""
    page.goto(BASE.format(y=y, m=m, p=PEOPLE), wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)

    grid = page.evaluate("""() => {
        const tables=[...document.querySelectorAll('table.tStyleC')];
        return tables.map(t=>[...t.rows].map(r=>[...r.cells].map(c=>({
            tx:(c.innerText||'').replace(/\\n/g,' ').trim(),
            imgs:[...c.querySelectorAll('img')].map(i=>i.alt)
        }))));
    }""")

    found = []
    for rows in grid:
        if not rows:
            continue
        header = [c["tx"] for c in rows[0]]
        times = [h for h in header if re.match(r"\d{1,2}:\d{2}", h)]
        if not times:
            continue
        cur_md = None
        for r in rows[1:]:
            # 日付セル（"7月1日(水)" 等）。無い行は直前の日付を引き継ぐ（コース複数行）
            for c in r:
                mm = re.search(r"(\d{1,2})月(\d{1,2})日", c["tx"])
                if mm:
                    cur_md = (int(mm.group(1)), int(mm.group(2)))
                    break
            if not cur_md:
                continue
            # コース名セル
            course = ""
            for c in r:
                if any(k in c["tx"] for k in ("コース", "SCHOOL", "体験")):
                    course = c["tx"].split(" ")[0].strip()
                    break
            if COURSE_KEYWORD and COURSE_KEYWORD not in course:
                continue
            # 時間セルは行末尾 len(times) 個
            slot_cells = r[-len(times):] if len(r) >= len(times) else r
            for i, c in enumerate(slot_cells):
                if not cell_bookable(c["imgs"]):
                    continue
                mo, da = cur_md
                try:
                    slot_date = date(_infer_year(mo), mo, da)
                except ValueError:
                    continue
                if slot_date < date.today():
                    continue
                t = times[i] if i < len(times) else "?"
                found.append((slot_date, course, t))
    return found


def check() -> list[str]:
    slots = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_context(user_agent=UA, locale="ja-JP").new_page()
        for (y, m) in _months_to_scan():
            try:
                for slot_date, course, t in scan_month(page, y, m):
                    label = f"{slot_date.month}月{slot_date.day}日 {course} {t}"
                    slots.add(label)
            except PWTimeout:
                print(f"[{y}/{m}] タイムアウト")
            except Exception as e:
                print(f"[{y}/{m}] エラー: {e}")
        browser.close()
    return sorted(slots)


# ===== 通知済み記録（リポジトリにコミットしてジョブ交代後も維持）=====
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notified_slots_jal.txt")


def load_seen() -> set:
    if os.environ.get("GITHUB_ACTIONS"):
        _git("pull", "--rebase", "--autostash")
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return {ln for ln in f.read().splitlines() if ln.strip()}
    except FileNotFoundError:
        return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(seen)))
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    _git("config", "user.name", "jal-bot")
    _git("config", "user.email", "bot@users.noreply.github.com")
    _git("add", SEEN_FILE)
    if _git("commit", "-m", "JAL通知済み枠を更新"):
        _git("pull", "--rebase", "--autostash")
        _git("push")


def _git(*args) -> bool:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True,
                           cwd=os.path.dirname(SEEN_FILE))
        return r.returncode == 0
    except Exception as e:
        print(f"git {args[0]} 失敗: {e}")
        return False


def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    seen = load_seen()
    available = check()
    print(f"検出した予約可能枠: {available}")

    new_slots = [s for s in available if f"JAL:{s}" not in seen]
    if new_slots:
        msg = (
            f"【JAL工場見学 SKY MUSEUM】\n"
            f"{PEOPLE}名で予約できる枠が出ました！🎉\n\n"
            + "\n".join(f"✅ {s}" for s in new_slots[:15])
            + f"\n\n今すぐ予約を！\n{ENTRY_URL}"
        )
        send_line_message(msg)
        save_seen(seen | {f"JAL:{s}" for s in new_slots})
        print(f"LINE通知送信完了: {new_slots}")
    else:
        print(f"新しい空きなし ({now_str}) / 既通知{len(seen)}件")


if __name__ == "__main__":
    main()
