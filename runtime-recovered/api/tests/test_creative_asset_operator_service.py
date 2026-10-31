from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PIL import Image

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.creative_asset_operator_service import CreativeAssetOperatorService
from atnos.services.runtime_settings_service import RuntimeSettingsService

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


class CreativeAssetOperatorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)
        bootstrap_test_app(self.app)

    def test_does_not_activate_for_regular_chat(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
        result = service.maybe_handle(
            content="napisz mi plan dnia",
            conversation_id=42,
            allow_local_file_write=True,
        )
        self.assertFalse(result.activated)

    def test_typoed_polish_graphic_request_still_routes_to_image_generation(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
        result = service.maybe_handle(
            content="stwóz mi grafię czerwonego kota",
            conversation_id=43,
            allow_local_file_write=True,
        )
        self.assertTrue(result.activated)
        self.assertEqual(result.selected_model_key, "canvas-composer-local")
        self.assertIsNotNone(result.artifact)
        artifact = result.artifact or {}
        self.assertEqual(artifact.get("file_extension"), ".png")

    def test_thumbnail_request_routes_to_local_canvas_and_creates_png(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
        result = service.maybe_handle(
            content="zrob miniature YouTube dla Atnosa z napisem Full Access Active",
            conversation_id=42,
            allow_local_file_write=True,
        )
        self.assertTrue(result.activated)
        self.assertEqual(result.selected_model_key, "canvas-composer-local")
        self.assertIsNotNone(result.artifact)
        artifact = result.artifact or {}
        self.assertEqual(artifact.get("file_extension"), ".png")
        self.assertTrue((self.root / str(artifact["storage_path"])).exists())
        summary = service.build_lane_summary()
        self.assertEqual(summary["last_output_lane_key"], service.CANVAS_COMPOSER_LANE)
        self.assertEqual(summary["last_output_filename"], artifact.get("filename"))
        self.assertTrue(str(summary["last_output_absolute_path"]).endswith(".png"))
        self.assertEqual(summary["last_output_width"], 1280)
        self.assertEqual(summary["last_output_height"], 720)
        self.assertEqual(summary["last_output_size_bytes"], artifact.get("size_bytes"))

    def test_generic_image_uses_comfyui_when_available(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
        captured: dict[str, object] = {}

        def fake_generate_background(*, output_path: Path, **kwargs):
            captured.update(kwargs)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (512, 512), "#223355").save(output_path)
            return None

        with patch.object(service.comfyui, "can_generate", return_value=True), patch.object(
            service.comfyui,
            "generate_background",
            side_effect=fake_generate_background,
        ):
            result = service.maybe_handle(
                content="wygeneruj fotorealistyczna ilustracje futurystycznego biurka Atnosa",
                conversation_id=77,
                allow_local_file_write=True,
            )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_model_key, "comfyui-local")
        self.assertIsNotNone(result.artifact)
        artifact = result.artifact or {}
        self.assertTrue((self.root / str(artifact["storage_path"])).exists())
        self.assertEqual(captured.get("steps_override"), 28)
        self.assertEqual(captured.get("cfg_scale_override"), 6.8)
        self.assertIn("photorealistic commercial key visual", str(captured.get("prompt")))

    def test_premium_quality_profile_pushes_comfyui_to_stronger_preset(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
        captured: dict[str, object] = {}

        def fake_generate_background(*, output_path: Path, **kwargs):
            captured.update(kwargs)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (512, 512), "#112244").save(output_path)
            return None

        with patch.object(service.comfyui, "can_generate", return_value=True), patch.object(
            service.comfyui,
            "generate_background",
            side_effect=fake_generate_background,
        ):
            result = service.maybe_handle(
                content="wygeneruj fotorealistyczna ilustracje futurystycznego biurka Atnosa",
                conversation_id=78,
                allow_local_file_write=True,
                quality_profile="premium",
            )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_model_key, "comfyui-local")
        self.assertEqual(captured.get("steps_override"), 34)
        self.assertEqual(captured.get("cfg_scale_override"), 7.4)
        self.assertIn("maximum detail fidelity", str(captured.get("prompt")))

    def test_reports_exact_blocker_for_photorealistic_image_without_image_model(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
        with patch.object(service.comfyui, "can_generate", return_value=False):
            result = service.maybe_handle(
                content="wygeneruj fotorealistyczna ilustracje studyjnego portretu founderki",
                conversation_id=12,
                allow_local_file_write=True,
            )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_model_key, "blocked")
        self.assertIn("Brak gotowego lane do generatywnej grafiki", result.assistant_content or "")

    def test_uses_openai_image_api_when_external_path_is_available(self) -> None:
        with self.app.state.database.session_scope() as session:
            accounts = AccountCredentialsService(session, self.app.state.settings)
            accounts.create_account(
                label="OpenAI",
                provider_key="openai-cloud",
                account_identifier="owner@example.com",
                secret_type="api_key",
                secret_value="sk-test-openai-image-1234567890",
                risk_level="medium",
                allowed_capabilities=["read_content"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
            service = CreativeAssetOperatorService(
                self.app.state.settings,
                account_credentials=accounts,
            )
            runtime_controls = RuntimeSettingsService(session, self.app.state.settings).snapshot()
            captured_payload: dict[str, object] = {}

            class _FakeResponse:
                status_code = 200
                text = '{"data":[{"b64_json":"..."}]}'

                @staticmethod
                def json() -> dict[str, object]:
                    return {
                        "data": [
                            {
                                "b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY0cAAAAASUVORK5CYII="
                            }
                        ]
                    }

            class _FakeHttpxClient:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

                def post(self, *args, **kwargs):
                    captured_payload.update(kwargs.get("json") or {})
                    return _FakeResponse()

            with patch.object(service.comfyui, "can_generate", return_value=False), patch(
                "atnos.services.creative_asset_operator_service.httpx.Client",
                _FakeHttpxClient,
            ):
                result = service.maybe_handle(
                    content="wygeneruj fotorealistyczna ilustracje cyberpunkowego laboratorium przez OpenAI",
                    conversation_id=88,
                    allow_local_file_write=True,
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_provider_key, "openai-cloud")
        self.assertTrue((result.selected_model_key or "").startswith("openai-image-api:"))
        self.assertIsNotNone(result.artifact)
        self.assertEqual(captured_payload.get("quality"), "high")
        self.assertIn("photorealistic premium still image", str(captured_payload.get("prompt")))

    def test_uses_ideogram_browser_lane_when_request_explicitly_prefers_browser(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)

        def fake_generate_browser(*, request, conversation_id, allow_external_fallback, runtime_controls):
            output_path = service.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
            Image.new("RGB", (512, 512), "#5522AA").save(output_path)
            return service.artifacts.finalize_output(
                absolute_path=output_path,
                filename="atnos-generic-image.png",
                media_type="image/png",
            )

        with patch.object(
            service,
            "_generate_with_ideogram_browser",
            side_effect=fake_generate_browser,
        ):
            result = service.maybe_handle(
                content="wygeneruj obraz futurystycznego miasta przez Ideogram w przegladarce",
                conversation_id=93,
                allow_local_file_write=True,
                allow_external_fallback=True,
            )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_provider_key, "ideogram-browser")
        self.assertEqual(result.selected_model_key, "ideogram-browser")
        self.assertIsNotNone(result.artifact)

    def test_falls_back_from_ideogram_browser_to_openai_image_api(self) -> None:
        with self.app.state.database.session_scope() as session:
            accounts = AccountCredentialsService(session, self.app.state.settings)
            accounts.create_account(
                label="OpenAI",
                provider_key="openai-cloud",
                account_identifier="owner@example.com",
                secret_type="api_key",
                secret_value="sk-test-openai-image-1234567890",
                risk_level="medium",
                allowed_capabilities=["read_content"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
            service = CreativeAssetOperatorService(
                self.app.state.settings,
                account_credentials=accounts,
            )
            runtime_controls = RuntimeSettingsService(session, self.app.state.settings).snapshot()

            class _FakeResponse:
                status_code = 200
                text = '{"data":[{"b64_json":"..."}]}'

                @staticmethod
                def json() -> dict[str, object]:
                    return {
                        "data": [
                            {
                                "b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY0cAAAAASUVORK5CYII="
                            }
                        ]
                    }

            class _FakeHttpxClient:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

                def post(self, *args, **kwargs):
                    return _FakeResponse()

            with patch.object(
                service,
                "_generate_with_ideogram_browser",
                side_effect=RuntimeError("Ideogram browser lane nie dowiozl grafiki: Cloudflare challenge aktywny."),
            ), patch(
                "atnos.services.creative_asset_operator_service.httpx.Client",
                _FakeHttpxClient,
            ), patch.object(service.comfyui, "can_generate", return_value=False):
                result = service.maybe_handle(
                    content="wygeneruj obraz futurystycznego miasta przez Ideogram w przegladarce",
                    conversation_id=94,
                    allow_local_file_write=True,
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_provider_key, "openai-cloud")
        self.assertTrue((result.selected_model_key or "").startswith("openai-image-api:"))
        self.assertIsNotNone(result.artifact)
        self.assertIn("Pierwotny tor `ideogram-browser` nie dowiozl zadania", result.assistant_content or "")

    def test_falls_back_from_openai_image_api_to_local_canvas_when_billing_is_blocked(self) -> None:
        with self.app.state.database.session_scope() as session:
            accounts = AccountCredentialsService(session, self.app.state.settings)
            accounts.create_account(
                label="OpenAI",
                provider_key="openai-cloud",
                account_identifier="owner@example.com",
                secret_type="api_key",
                secret_value="sk-test-openai-image-1234567890",
                risk_level="medium",
                allowed_capabilities=["read_content"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
            service = CreativeAssetOperatorService(
                self.app.state.settings,
                account_credentials=accounts,
            )
            runtime_controls = RuntimeSettingsService(session, self.app.state.settings).snapshot()

            with patch.object(
                service,
                "_generate_with_ideogram_browser",
                side_effect=RuntimeError("Ideogram browser lane nie dowiozl grafiki: Cloudflare challenge aktywny."),
            ), patch.object(
                service,
                "_generate_with_openai_image_api",
                side_effect=RuntimeError("Billing hard limit has been reached."),
            ), patch.object(service.comfyui, "can_generate", return_value=False):
                result = service.maybe_handle(
                    content="wygeneruj obraz futurystycznego miasta przez Ideogram w przegladarce",
                    conversation_id=95,
                    allow_local_file_write=True,
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_provider_key, "creative-asset-operator")
        self.assertEqual(result.selected_model_key, "canvas-composer-local")
        self.assertIsNotNone(result.artifact)
        self.assertIn("Cloud image API nie dowiozl obrazu", result.assistant_content or "")

    def test_clear_remembered_lane_blockers_keeps_ready_entries(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
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
            status="degraded",
            reason="OpenAI image API chwilowo zwrocilo blad transportowy.",
        )

        cleared = service.clear_remembered_lane_blockers()

        self.assertEqual(
            set(cleared),
            {service.IDEOGRAM_BROWSER_LANE, service.OPENAI_IMAGE_API_LANE},
        )
        cache = service._load_lane_status_cache()
        self.assertIn(service.CANVAS_COMPOSER_LANE, cache)
        self.assertNotIn(service.IDEOGRAM_BROWSER_LANE, cache)
        self.assertNotIn(service.OPENAI_IMAGE_API_LANE, cache)

    def test_clear_remembered_lane_blockers_can_target_specific_lanes(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
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

        cleared = service.clear_remembered_lane_blockers(
            lane_keys=(service.IDEOGRAM_BROWSER_LANE,),
        )

        self.assertEqual(cleared, [service.IDEOGRAM_BROWSER_LANE])
        cache = service._load_lane_status_cache()
        self.assertNotIn(service.IDEOGRAM_BROWSER_LANE, cache)
        self.assertIn(service.OPENAI_IMAGE_API_LANE, cache)

    def test_build_lane_summary_exposes_screenshot_path_for_latest_blocker(self) -> None:
        service = CreativeAssetOperatorService(self.app.state.settings)
        screenshot_path = str((self.root / "runtime" / "ideogram-blocker.png").resolve())
        service._record_lane_status(
            service.IDEOGRAM_BROWSER_LANE,
            status="blocked",
            reason=(
                "Ideogram browser lane nie dowiozl grafiki: Cloudflare challenge aktywny. "
                f"Screenshot: {screenshot_path}"
            ),
        )

        summary = service.build_lane_summary()

        self.assertEqual(summary["last_blocker_lane_key"], service.IDEOGRAM_BROWSER_LANE)
        self.assertEqual(
            summary["last_blocker_summary"],
            "Ideogram browser lane nie dowiozl grafiki: Cloudflare challenge aktywny.",
        )
        self.assertEqual(summary["last_blocker_screenshot_path"], screenshot_path)


if __name__ == "__main__":
    unittest.main()
