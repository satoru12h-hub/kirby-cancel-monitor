"""ポケモンセンターオンラインの新しい抽選案内をLINEへ通知する。"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen


HOME_URL = "https://www.pokemoncenter-online.com/"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
STATE_PATH = Path(__file__).with_name("pokemon_news_seen.txt")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class NewsLinkParser(HTMLParser):
    """トップページ内の /news/?id=... リンクを抽出する。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self._href: str | None = None
        self._news_id: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        href = dict(attrs).get("href")
        if not href:
            return
        absolute = urljoin(HOME_URL, href)
        parsed = urlparse(absolute)
        if parsed.netloc != "www.pokemoncenter-online.com" or parsed.path != "/news/":
            return
        news_id = parse_qs(parsed.query).get("id", [None])[0]
        if not news_id:
            return
        self._href = absolute
        self._news_id = news_id
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None or self._news_id is None:
            return
        title = re.sub(r"\s+", " ", html.unescape(" ".join(self._text))).strip()
        self.items.append({"id": self._news_id, "url": self._href, "title": title})
        self._href = None
        self._news_id = None
        self._text = []


class MainTextParser(HTMLParser):
    """ニュース詳細ページの main 要素だけをテキスト化する。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._main_depth = 0
        self._ignored_depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "main":
            self._main_depth += 1
        elif self._main_depth and tag in {"script", "style"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._main_depth and tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == "main" and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._main_depth and not self._ignored_depth:
            self._text.append(data)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._text)).strip()


def fetch_text(url: str, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ja-JP,ja;q=0.9"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"取得に失敗しました: {url} ({exc})") from exc


def parse_news_links(page_html: str) -> list[dict[str, str]]:
    parser = NewsLinkParser()
    parser.feed(page_html)
    unique: dict[str, dict[str, str]] = {}
    for item in parser.items:
        unique.setdefault(item["id"], item)
    return list(unique.values())


def extract_main_text(page_html: str) -> str:
    parser = MainTextParser()
    parser.feed(page_html)
    return parser.text


def is_lottery_announcement(item: dict[str, str]) -> bool:
    if "抽選" in item["title"]:
        return True
    detail_html = fetch_text(item["url"])
    return "抽選" in extract_main_text(detail_html)


def load_seen() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    return {
        line.strip()
        for line in STATE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def save_seen(seen: set[str]) -> None:
    STATE_PATH.write_text("".join(f"{news_id}\n" for news_id in sorted(seen, reverse=True)), encoding="utf-8")


def send_line_message(text: str) -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN または LINE_USER_ID が設定されていません")

    payload = json.dumps(
        {"to": user_id, "messages": [{"type": "text", "text": text}]},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        LINE_PUSH_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"LINE送信に失敗しました: HTTP {response.status}")
            print(f"LINE送信成功: HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"LINE送信に失敗しました: {exc}") from exc


def initialize() -> None:
    items = parse_news_links(fetch_text(HOME_URL))
    if not items:
        raise RuntimeError("お知らせリンクを1件も取得できませんでした")
    save_seen({item["id"] for item in items})
    print(f"初期化完了: 現在のお知らせ {len(items)} 件を既知として登録")


def monitor_once() -> None:
    items = parse_news_links(fetch_text(HOME_URL))
    if not items:
        raise RuntimeError("お知らせリンクを1件も取得できませんでした")

    seen = load_seen()
    if not seen:
        raise RuntimeError("状態ファイルが未初期化です。--initialize を一度実行してください")

    new_items = [item for item in items if item["id"] not in seen]
    if not new_items:
        print("新しいお知らせはありません")
        return

    for item in reversed(new_items):
        if is_lottery_announcement(item):
            message = (
                "【ポケモンセンターオンライン 抽選案内】\n"
                f"{item['title']}\n"
                f"{item['url']}"
            )
            send_line_message(message)
            print(f"抽選案内を通知: {item['id']} {item['title']}")
        else:
            print(f"抽選以外のお知らせを確認: {item['id']} {item['title']}")
        seen.add(item["id"])
        save_seen(seen)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize", action="store_true", help="現在のお知らせを通知せず既知として登録")
    parser.add_argument("--send-test", action="store_true", help="LINEへテスト通知を送信")
    args = parser.parse_args()

    if args.initialize:
        initialize()
    elif args.send_test:
        send_line_message(
            "【ポケモンセンターオンライン 抽選監視】\n"
            "設定が完了しました。今後、新しい抽選案内が掲載されたらこのLINEへお知らせします。"
        )
    else:
        monitor_once()


if __name__ == "__main__":
    main()
