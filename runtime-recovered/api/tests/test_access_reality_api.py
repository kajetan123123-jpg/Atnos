from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

from atnos.services.creative_asset_operator_service import CreativeAssetOperatorService

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir


class AccessRealityApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_bootstrap_and_settings_payloads_include_access_reality(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            bootstrap = client.get("/ui/bootstrap")
            self.assertEqual(bootstrap.status_code, 200)
            bootstrap_payload = bootstrap.json()
            self.assertIn("access_reality", bootstrap_payload)
            self.assertIn("subjects", bootstrap_payload["access_reality"])
            self.assertIn("summary", bootstrap_payload["access_reality"])

            settings = client.get("/settings/control-center")
            self.assertEqual(settings.status_code, 200)
            settings_payload = settings.json()
            self.assertIn("access_reality", settings_payload)
            self.assertIn("subjects", settings_payload["access_reality"])
            self.assertIn("summary", settings_payload["access_reality"])
            self.assertIn("creative_asset_lanes", settings_payload)
            self.assertGreaterEqual(len(settings_payload["creative_asset_lanes"]), 1)
            self.assertIn("creative_asset_lane_summary", settings_payload)
            self.assertIn(
                "last_blocker_screenshot_path",
                settings_payload["creative_asset_lane_summary"],
            )
            self.assertIn(
                "last_output_absolute_path",
                settings_payload["creative_asset_lane_summary"],
            )
            self.assertIn(
                "last_output_width",
                settings_payload["creative_asset_lane_summary"],
            )
            self.assertIn(
                "last_output_height",
                settings_payload["creative_asset_lane_summary"],
            )
            self.assertIn(
                "last_output_size_bytes",
                settings_payload["creative_asset_lane_summary"],
            )

    def test_bootstrap_payload_allows_preview_origin_via_cors(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            response = client.get(
                "/ui/bootstrap",
                headers={"Origin": "http://127.0.0.1:4174"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers.get("access-control-allow-origin"),
                "http://127.0.0.1:4174",
            )

    def test_shell_payload_keeps_first_paint_lightweight(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            response = client.get("/ui/shell")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("ui_version", payload)
        self.assertIn("generated_at", payload)
        self.assertIn("projects", payload)
        self.assertIn("recent_conversations", payload)
        self.assertNotIn("provider_cards", payload)
        self.assertNotIn("trusted_auto_summary", payload)
        self.assertNotIn("access_reality", payload)

    def test_creative_asset_lane_actions_refresh_control_center_payload(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        service = CreativeAssetOperatorService(app.state.settings)
        service._record_lane_status(
            service.CANVAS_COMPOSER_LANE,
            status="ready",
            reason="Lokalny canvas composer zwrocil gotowy PNG.",
        )
        service._record_lane_status(
            service.IDEOGRAM_BROWSER_LANE,
            status="blocked",
            reason="Ideogram browser lane nie dowiozl grafiki: Cloudflare challenge aktywny.",
        )
        service._record_lane_status(
            service.OPENAI_IMAGE_API_LANE,
            status="blocked",
            reason="Billing hard limit has been reached.",
        )

        with TestClient(app) as client:
            retest = client.post("/settings/control-center/creative-assets/retest")
            self.assertEqual(retest.status_code, 200)
            retest_payload = retest.json()
            lanes_by_key = {
                item["lane_key"]: item for item in retest_payload["creative_asset_lanes"]
            }
            self.assertEqual(
                lanes_by_key[service.CANVAS_COMPOSER_LANE]["last_recorded_reason"],
                "Lokalny canvas composer zwrocil gotowy PNG.",
            )
            self.assertEqual(
                lanes_by_key[service.IDEOGRAM_BROWSER_LANE]["last_recorded_reason"],
                "",
            )
            self.assertEqual(
                lanes_by_key[service.OPENAI_IMAGE_API_LANE]["last_recorded_reason"],
                "",
            )

            service._record_lane_status(
                service.IDEOGRAM_BROWSER_LANE,
                status="blocked",
                reason="Ideogram browser lane nie dowiozl grafiki: Cloudflare challenge aktywny.",
            )
            clear = client.post("/settings/control-center/creative-assets/clear-blockers")
            self.assertEqual(clear.status_code, 200)
            clear_payload = clear.json()
            lanes_by_key = {
                item["lane_key"]: item for item in clear_payload["creative_asset_lanes"]
            }
            self.assertEqual(
                lanes_by_key[service.IDEOGRAM_BROWSER_LANE]["last_recorded_reason"],
                "",
            )
            self.assertEqual(
                lanes_by_key[service.CANVAS_COMPOSER_LANE]["last_recorded_reason"],
                "Lokalny canvas composer zwrocil gotowy PNG.",
            )


if __name__ == "__main__":
    unittest.main()
