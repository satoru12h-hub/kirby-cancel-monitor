"""ANA Blue Hangar Tour（ANA機体工場見学）の空き監視。
指定人数（既定4名）分の残席がある枠が出たらLINE通知する。
予約サイトはresv.jp系。月カレンダーをajaxで操作し、各枠の「残N」を読む。
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
PEOPLE = 4                 # 何名分の残席を探すか（残N >= PEOPLE で通知）
MONTHS_AHEAD = 1           # 当月から何ヶ月先まで見るか（予約受付は1ヶ月先まで）
# ====================

CAL_URL = "https://ana-blue-hangar-tour.resv.jp/reserve/calendar.php"
ENTRY_URL = "https://ana-blue-hangar-tour.resv.jp/"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 1ヶ月分のカレンダーから (date, time, remaining) を抜き出すJS
EXTRACT_JS = r"""() => {
  const lbl = (document.querySelector('#period_area')||{}).innerText || '';
  const uls = [...document.querySelectorAll('ul.data-month')];
  const slots = [];
  uls.forEach(ul => {
    const blk = (ul.querySelector('.data-month-block')||{}).innerText || '';
    const rem = blk.match(/残(\d+)/);
    const tm  = blk.match(/(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})/);
    // 日付セルの span.month-to-day onclick="changeViewModeDay(Y,M,D)" から正確な日付
    const td = ul.closest('td');
    let ymd = null;
    if (td) {
      const sp = td.querySelector('span.month-to-day[onclick]');
      if (sp) {
        const mm = (sp.getAttribute('onclick')||'').match(/changeViewModeDay\((\d+),(\d+),(\d+)\)/);
        if (mm) ymd = [ +mm[1], +mm[2], +mm[3] ];
      }
    }
    if (rem && tm && ymd) {
      slots.push({ y:ymd[0], m:ymd[1], d:ymd[2], time:tm[1].replace(/\s+/g,''), rem:+rem[1] });
    }
  });
  return { label: lbl.replace(/\s+/g,''), n_slots: uls.length, slots };
}"""


def send_line_message(text: str):
    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}",
                 "Content-Type": "application/json"},
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]},
    )
    print(f"LINE送信結果: {resp.status_code} {resp.text[:200]}")


def _goto_next_month(page, prev_label: str) -> bool:
    """翌月へ。ラベルが変わったらTrue。"""
    try:
        page.click("#next a", timeout=8000)
    except Exception as e:
        print(f"翌月クリック失敗: {e}")
        return False
    for _ in range(25):
        page.wait_for_timeout(400)
        cur = page.evaluate("()=>(document.querySelector('#period_area')||{}).innerText||''").replace(" ", "").strip()
        if cur and cur != prev_label:
            page.wait_for_timeout(600)
            return True
    return False


def check():
    """戻り値: (空き枠リスト, 正常に読めたか)。
    カレンダーの枠(ul.data-month)が一度も取れなければ healthy=False。"""
    slots = set()
    months_seen = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_context(user_agent=UA, locale="ja-JP").new_page()
        page.goto(CAL_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)

        prev_label = ""
        for i in range(MONTHS_AHEAD + 1):
            info = page.evaluate(EXTRACT_JS)
            if info["n_slots"] > 0:
                months_seen += 1
            print(f"[{info['label']}] 枠{info['n_slots']} / 残席表示{len(info['slots'])}件")
            for s in info["slots"]:
                if s["rem"] < PEOPLE:
                    continue
                try:
                    slot_date = date(s["y"], s["m"], s["d"])
                except ValueError:
                    continue
                if slot_date < date.today():
                    continue
                slots.add(f"{s['m']}月{s['d']}日 {s['time']}（残{s['rem']}）")
            if i < MONTHS_AHEAD:
                if not _goto_next_month(page, info["label"]):
                    break
                prev_label = info["label"]
        browser.close()
    healthy = months_seen > 0
    print(f"読み取れた月: {months_seen} / 正常={healthy}")
    return sorted(slots), healthy


# ===== 通知済み記録・自己点検（リポジトリにコミットして永続化）=====
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notified_slots_ana.txt")
HEALTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ana_health.txt")


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
    _commit(os.path.basename(SEEN_FILE), "ANA通知済み枠を更新")


def _read_state(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _write_state(path: str, value: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)
    _commit(os.path.basename(path), "ANA自己点検の状態を更新")


def _commit(filename: str, msg: str):
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    _git("config", "user.name", "ana-bot")
    _git("config", "user.email", "bot@users.noreply.github.com")
    _git("add", filename)
    if _git("commit", "-m", msg):
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


def report_health(healthy: bool):
    today = date.today().isoformat()
    prev = _read_state(HEALTH_FILE)
    if not healthy:
        if prev == f"ng:{today}":
            print("異常継続中（本日アラート済み）")
            return
        send_line_message(
            "⚠️【ANA Blue Hangar Tour 監視】\n"
            "予約ページをいつも通り読み取れませんでした。\n"
            "アクセス制限やサイトの仕様変更の可能性があります。"
            "（空き通知が止まっているおそれ）\n"
            "しばらく自動で再試行します。"
        )
        _write_state(HEALTH_FILE, f"ng:{today}")
        print("異常アラートを送信")
    else:
        if prev.startswith("ng:"):
            send_line_message("✅【ANA Blue Hangar Tour 監視】ページの読み取りが復旧しました。監視を継続します。")
            print("復旧通知を送信")
        _write_state(HEALTH_FILE, "ok")


def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    seen = load_seen()
    available, healthy = check()
    print(f"検出した予約可能枠: {available}")

    report_health(healthy)

    new_slots = [s for s in available if f"ANA:{s}" not in seen]
    if new_slots:
        msg = (
            f"【ANA Blue Hangar Tour 工場見学】\n"
            f"{PEOPLE}名で予約できる枠が出ました！🎉\n\n"
            + "\n".join(f"✅ {s}" for s in new_slots[:15])
            + f"\n\n今すぐ予約を！\n{ENTRY_URL}"
        )
        send_line_message(msg)
        save_seen(seen | {f"ANA:{s}" for s in new_slots})
        print(f"LINE通知送信完了: {new_slots}")
    else:
        print(f"新しい空きなし ({now_str}) / 既通知{len(seen)}件")


if __name__ == "__main__":
    main()
