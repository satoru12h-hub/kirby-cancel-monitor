import unittest

from pokemon_lottery_monitor import extract_main_text, parse_news_links


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


if __name__ == "__main__":
    unittest.main()
