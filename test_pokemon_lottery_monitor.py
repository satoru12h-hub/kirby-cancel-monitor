import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from pokemon_lottery_monitor import (
    add_application_reminders,
    extract_application_starts,
    extract_main_text,
    parse_news_links,
)


class PokemonLotteryMonitorTest(unittest.TestCase):
    def test_parse_news_links_deduplicates_and_normalizes_text(self):
        source = """
        <a href="/news/?id=20260803">
          <span class="date">2026年08月03日</span>
          <span class="ttl">抽選期間と応募方法について</span>
        </a>
        <a href="https://www.pokemoncenter-online.com/news/?id=20260803">duplicate</a>
        <a href="/products/1">商品</a>
        """
        self.assertEqual(
            parse_news_links(source),
            [
                {
                    "id": "20260803",
                    "url": "https://www.pokemoncenter-online.com/news/?id=20260803",
                    "title": "2026年08月03日 抽選期間と応募方法について",
                }
            ],
        )

    def test_extract_main_text_ignores_navigation_and_scripts(self):
        source = """
        <nav>過去の抽選案内</nav>
        <main><h1>新商品</h1><script>抽選</script><p>通常販売です。</p></main>
        <footer>抽選販売FAQ</footer>
        """
        self.assertEqual(extract_main_text(source), "新商品 通常販売です。")

    def test_extracts_multiple_application_starts(self):
        source = """
        <main>
          <h1>抽選期間と応募方法について</h1><p>2026年08月03日（月）</p>
          <p>・抽選応募受付期間<br>8月10日（月）12時00分～8月14日（金）16時59分</p>
          <p>・抽選応募受付期間<br>8月28日（金）12時00分～8月31日（月）16時59分</p>
        </main>
        """
        self.assertEqual(
            extract_application_starts(source),
            [
                datetime(2026, 8, 10, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
                datetime(2026, 8, 28, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
            ],
        )

    def test_adds_only_future_reminders_at_one_hour_after_start(self):
        item = {
            "id": "20260803",
            "url": "https://www.pokemoncenter-online.com/news/?id=20260803",
            "title": "抽選期間と応募方法について",
        }
        source = """
        <main><p>2026年08月03日（月）</p>
        <p>抽選応募受付期間 8月10日（月）12時00分～8月14日（金）16時59分</p>
        <p>抽選応募受付期間 8月28日（金）12時00分～8月31日（月）16時59分</p></main>
        """
        reminders = {}
        added = add_application_reminders(
            item,
            source,
            reminders,
            now=datetime(2026, 8, 17, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        )
        self.assertEqual(added, 1)
        reminder = next(iter(reminders.values()))
        self.assertEqual(reminder["start_at"], "2026-08-28T12:00:00+09:00")
        self.assertEqual(reminder["notify_at"], "2026-08-28T13:00:00+09:00")
        self.assertFalse(reminder["sent"])


if __name__ == "__main__":
    unittest.main()
