from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.services.trend_watch_service import GOOGLE_TRENDS_RSS_URL, TIKTOK_CREATIVE_CENTER_URL, TrendWatchService


GOOGLE_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:ht="https://trends.google.com/trending/rss">
  <channel>
    <item>
      <title>Late night story drop</title>
      <link>https://example.com/story</link>
      <ht:approx_traffic>200K+</ht:approx_traffic>
      <ht:news_item>
        <ht:news_item_title>Viral late night story</ht:news_item_title>
      </ht:news_item>
    </item>
    <item>
      <title>Behind the scene fashion clip</title>
      <link>https://example.com/fashion</link>
      <ht:approx_traffic>120K+</ht:approx_traffic>
      <ht:news_item>
        <ht:news_item_title>Fashion clip trends in Poland</ht:news_item_title>
      </ht:news_item>
    </item>
  </channel>
</rss>
"""

TIKTOK_SAMPLE_HTML = """
<html>
  <body>
    <script id="__NEXT_DATA__" type="application/json">
      {
        "props": {
          "pageProps": {
            "dehydratedState": {
              "queries": [
                {"state": {"data": {}}},
                {
                  "state": {
                    "data": {
                      "pages": [
                        {
                          "list": [
                            {
                              "hashtagName": "latenight",
                              "rank": 1,
                              "publishCnt": 42,
                              "videoViews": 1200000,
                              "industryInfo": {"value": "entertainment"}
                            },
                            {
                              "hashtagName": "storytime",
                              "rank": 2,
                              "publishCnt": 31,
                              "videoViews": 900000,
                              "industryInfo": {"value": "lifestyle"}
                            }
                          ]
                        }
                      ]
                    }
                  }
                }
              ]
            }
          }
        }
      }
    </script>
  </body>
</html>
"""


class TrendWatchServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)
        self.vault_dir = self.root / "obsydian"
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.app.state.settings.obsidian_vault_path = self.vault_dir
        self.app.state.settings.obsidian_export_enabled = True

    def test_run_watch_collects_public_signals_and_writes_obsidian_note(self) -> None:
        def fake_fetch(url: str) -> str:
            if url == GOOGLE_TRENDS_RSS_URL.format(region="PL"):
                return GOOGLE_SAMPLE_RSS
            if url == TIKTOK_CREATIVE_CENTER_URL:
                return TIKTOK_SAMPLE_HTML
            raise AssertionError(f"Unexpected URL: {url}")

        with TestClient(self.app):
            with self.app.state.database.session_scope() as session:
                service = TrendWatchService(session, self.app.state.settings)
                with patch.object(service, "_fetch_text", side_effect=fake_fetch):
                    payload = service.run_watch(force=True)
                session.commit()

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["region_code"], "PL")
        self.assertIn("story", payload["summary"]["top_keywords"])
        self.assertIn("#latenight", payload["summary"]["top_hashtags"])
        self.assertIn("micro_story", payload["summary"]["recommended_reel_types"])
        self.assertEqual(payload["note_path"], "Atnos/Inbox/Trends.md")

        note_path = self.vault_dir / "Atnos" / "Inbox" / "Trends.md"
        self.assertTrue(note_path.exists())
        markdown = note_path.read_text(encoding="utf-8")
        self.assertIn("# Trends", markdown)
        self.assertIn("#latenight", markdown)


if __name__ == "__main__":
    unittest.main()
