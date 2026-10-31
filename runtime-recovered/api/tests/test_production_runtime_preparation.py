from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.services.comfyui_runtime_service import ComfyUiInstallState, ComfyUiRuntimeReadiness
from atnos.services.ollama_model_roles_service import OllamaModelRoleSnapshot
from atnos.services.production_runtime_preparation_service import ProductionRuntimePreparationService
from test_support import create_test_app


def _ollama_snapshot(*, ready: bool = False) -> OllamaModelRoleSnapshot:
    return OllamaModelRoleSnapshot(
        status="ready" if ready else "missing_main_model",
        ready=ready,
        main_text_model="qwen3:14b",
        fast_text_model="qwen3:8b",
        vision_model="qwen3-vl:8b",
        installed_models=("qwen3:8b", "qwen3-vl:8b"),
        missing_models=() if ready else ("qwen3:14b",),
        active_text_model="qwen3:14b" if ready else "qwen3:8b",
        active_vision_model="qwen3-vl:8b",
        reason="ok" if ready else "Main text model is missing.",
        suggestions=() if ready else ("ollama pull qwen3:14b",),
        checked_at=datetime.now(UTC),
    )


async def _fake_readiness_check(self, *, include_live_checks: bool = True):
    return {
        "ready": False,
        "status": "needs_configuration",
        "blocking": ["youtube_oauth"],
        "next_actions": ["Wrzuć Google client_secret.json i połącz YouTube Studio."],
        "items": [],
    }


async def _fake_readiness_check_with_publish_missing(self, *, include_live_checks: bool = True):
    return {
        "ready": False,
        "status": "needs_configuration",
        "blocking": ["youtube_oauth"],
        "next_actions": ["Wrzuc Google client_secret.json i polacz YouTube Studio."],
        "items": [
            {"key": "youtube_oauth", "ready": False, "status": "missing"},
            {"key": "scheduler", "ready": True, "status": "ready"},
        ],
    }


def test_prepare_runtime_reports_actions_without_autostarting_comfyui(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    service = ProductionRuntimePreparationService(app.state.settings)

    with patch(
        "atnos.services.production_runtime_preparation_service.OllamaModelRolesService.probe",
        return_value=_ollama_snapshot(ready=False),
    ), patch(
        "atnos.services.production_runtime_preparation_service.ComfyUiRuntimeService._server_ready",
        return_value=False,
    ), patch(
        "atnos.services.production_runtime_preparation_service.ComfyUiRuntimeService.describe_install",
    ) as describe_install, patch(
        "atnos.services.production_runtime_preparation_service.ProductionRuntimePreparationService._models_status",
    ) as models_status, patch(
        "atnos.services.production_runtime_preparation_service.ShortProductionReadinessService.check",
        _fake_readiness_check_with_publish_missing,
    ):
        install_dir = tmp_path / "ComfyUI_windows_portable"
        main_script = install_dir / "ComfyUI" / "main.py"
        python_executable = install_dir / "python_embeded" / "python.exe"
        checkpoint_dir = install_dir / "ComfyUI" / "models" / "checkpoints"
        describe_install.return_value = ComfyUiInstallState(
            install_dir=install_dir,
            run_script=None,
            fallback_script=None,
            python_executable=python_executable,
            main_script=main_script,
            checkpoint_dir=checkpoint_dir,
            exists=True,
        )
        models_status.return_value = {
            "ready": False,
            "pipelines": {
                "shorts_sdxl": {
                    "model_ids": ["sdxl_base_vae"],
                    "ready": False,
                    "missing": ["sdxl_base_vae"],
                },
                "shorts_full": {
                    "model_ids": ["sdxl_base_vae", "ltx_video"],
                    "ready": False,
                    "missing": ["ltx_video"],
                }
            },
            "vram": {"free_gb": 16},
            "auto_download_endpoint": "POST /api/models/ensure-pipeline/shorts_full?wait=true",
        }
        result = __import__("asyncio").run(service.prepare(start_comfyui=False, comfyui_wait_timeout_s=5))

    payload = result.to_dict()
    assert payload["status"] == "needs_action"
    assert payload["local_runtime_ready"] is False
    assert payload["publish_ready"] is False
    assert payload["full_pipeline_ready"] is False
    assert payload["ollama"]["missing_models"] == ["qwen3:14b"]
    assert payload["comfyui"]["image_generation"] is False
    assert payload["comfyui"]["install_dir"] == str(install_dir)
    assert payload["comfyui"]["main_script"] == str(main_script)
    assert payload["comfyui"]["python_executable"] == str(python_executable)
    assert payload["comfyui"]["checkpoint_dir"] == str(checkpoint_dir)
    assert payload["models"]["pipelines"]["shorts_full"]["missing"] == ["ltx_video"]
    assert payload["next_actions"][0].startswith("Jednym krokiem przygotuj lokalny runtime")
    assert "POST /api/production/runtime/prepare" in payload["next_actions"][0]
    assert '"ensure_models": true' in payload["next_actions"][0]
    assert '"ensure_ollama_models": false' in payload["next_actions"][0]
    assert "ollama pull qwen3:14b" in payload["next_actions"]
    assert any("ComfyUI" in action for action in payload["next_actions"])
    assert any("/api/models/ensure-pipeline/shorts_sdxl?wait=true" in action for action in payload["next_actions"])
    assert any("client_secret.json" in action for action in payload["next_actions"])


def test_models_status_reports_ltx_and_wan_readiness_separately(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    service = ProductionRuntimePreparationService(app.state.settings)

    async def fake_get_status(self):
        return {
            "models": {
                "sdxl_base_vae": {"status": "ok"},
                "ltx_video": {"status": "ok"},
                "ltx_vae": {"status": "missing"},
                "t5_encoder": {"status": "ok"},
                "wan_video_1_3b": {"status": "missing"},
                "kokoro_v1": {"status": "ok"},
                "kokoro_voices_pl": {"status": "ok"},
                "whisper_medium": {"status": "missing"},
            },
            "pipelines": {
                "shorts_sdxl": ["sdxl_base_vae"],
                "shorts_ltx": ["ltx_video", "ltx_vae", "t5_encoder"],
                "shorts_wan": ["wan_video_1_3b"],
                "shorts_full": ["sdxl_base_vae", "ltx_video", "ltx_vae", "t5_encoder", "wan_video_1_3b"],
                "tts": ["kokoro_v1", "kokoro_voices_pl"],
                "subtitles": ["whisper_medium"],
            },
            "vram": {"free_gb": 12.5},
        }

    with patch("atnos.model_manager.model_manager.ModelManager.get_status", fake_get_status):
        payload = __import__("asyncio").run(service._models_status())

    assert payload["ready"] is True
    assert payload["required_pipeline"] == "shorts_sdxl"
    assert payload["pipelines"]["shorts_sdxl"]["ready"] is True
    assert payload["pipelines"]["shorts_ltx"]["ready"] is False
    assert payload["pipelines"]["shorts_ltx"]["missing"] == ["ltx_vae"]
    assert payload["pipelines"]["shorts_wan"]["ready"] is False
    assert payload["pipelines"]["shorts_wan"]["missing"] == ["wan_video_1_3b"]
    assert payload["pipelines"]["tts"]["ready"] is True
    assert payload["pipelines"]["subtitles"]["missing"] == ["whisper_medium"]
    assert payload["auto_download_endpoints"]["shorts_wan"] == "POST /api/models/ensure-pipeline/shorts_wan?wait=true"


def test_models_status_prefers_model_manager_pipeline_statuses(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    service = ProductionRuntimePreparationService(app.state.settings)

    async def fake_get_status(self):
        return {
            "models": {},
            "pipelines": {
                "shorts_full": ["sdxl_base_vae", "ltx_video"],
            },
            "pipeline_statuses": {
                "shorts_full": {
                    "ready": False,
                    "model_ids": ["sdxl_base_vae", "ltx_video"],
                    "missing": ["ltx_video"],
                    "download_endpoint": "POST /api/models/ensure-pipeline/shorts_full?wait=true",
                    "download_status_endpoint": "GET /api/models/downloads/shorts_full",
                },
                "shorts_wan": {
                    "ready": True,
                    "model_ids": ["wan_video_1_3b"],
                    "missing": [],
                    "download_endpoint": "POST /api/models/ensure-pipeline/shorts_wan?wait=true",
                    "download_status_endpoint": "GET /api/models/downloads/shorts_wan",
                },
            },
            "vram": {"free_gb": 12.5},
        }

    with patch("atnos.model_manager.model_manager.ModelManager.get_status", fake_get_status):
        payload = __import__("asyncio").run(service._models_status())

    assert payload["ready"] is False
    assert payload["pipelines"]["shorts_full"]["missing"] == ["ltx_video"]
    assert payload["pipelines"]["shorts_full"]["download_endpoint"] == (
        "POST /api/models/ensure-pipeline/shorts_full?wait=true"
    )
    assert payload["pipelines"]["shorts_full"]["download_status_endpoint"] == "GET /api/models/downloads/shorts_full"
    assert payload["pipelines"]["shorts_wan"]["ready"] is True
    assert payload["pipelines"]["shorts_wan"]["missing"] == []


def test_prepare_runtime_can_auto_download_shorts_full_models(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    service = ProductionRuntimePreparationService(app.state.settings)
    calls: list[str] = []

    async def fake_ensure_pipeline_models(self, pipeline: str):
        calls.append(pipeline)
        return {
            "sdxl_base_vae": True,
            "ltx_video": True,
            "ltx_vae": True,
            "t5_encoder": True,
            "wan_video_1_3b": True,
        }

    async def fake_models_status(self, *, required_pipeline: str = "shorts_sdxl"):
        assert required_pipeline == "shorts_full"
        return {
            "ready": True,
            "pipelines": {"shorts_full": {"ready": True, "missing": []}},
            "vram": {"free_gb": 16},
            "auto_download_endpoint": "POST /api/models/ensure-pipeline/shorts_full?wait=true",
        }

    with patch(
        "atnos.services.production_runtime_preparation_service.OllamaModelRolesService.probe",
        return_value=_ollama_snapshot(ready=True),
    ), patch(
        "atnos.services.production_runtime_preparation_service.ProductionRuntimePreparationService._prepare_comfyui",
        return_value=ComfyUiRuntimeReadiness(image_generation=True, detail="ready"),
    ), patch(
        "atnos.services.production_runtime_preparation_service.ModelManager.ensure_pipeline_models",
        fake_ensure_pipeline_models,
    ), patch(
        "atnos.services.production_runtime_preparation_service.ProductionRuntimePreparationService._models_status",
        fake_models_status,
    ), patch(
        "atnos.services.production_runtime_preparation_service.ShortProductionReadinessService.check",
        _fake_readiness_check_with_publish_missing,
    ):
        result = __import__("asyncio").run(
            service.prepare(ensure_models=True, model_pipeline="shorts_full")
        )

    payload = result.to_dict()
    assert calls == ["shorts_full"]
    assert payload["status"] == "local_runtime_ready"
    assert payload["local_runtime_ready"] is True
    assert payload["publish_ready"] is False
    assert payload["full_pipeline_ready"] is False
    assert payload["models"]["auto_download"]["requested"] is True
    assert payload["models"]["auto_download"]["status"] == "ready"
    assert payload["models"]["auto_download"]["results"]["wan_video_1_3b"] is True


def test_prepare_runtime_can_pull_missing_ollama_models(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    service = ProductionRuntimePreparationService(app.state.settings)
    snapshots = [_ollama_snapshot(ready=False), _ollama_snapshot(ready=True)]
    pull_calls: list[tuple[str, ...]] = []

    def fake_probe(self):
        return snapshots.pop(0)

    def fake_pull_missing(self, snapshot):
        pull_calls.append(tuple(snapshot.missing_models))
        return {
            "requested": True,
            "status": "ready",
            "results": {model: {"ok": True, "status": "success"} for model in snapshot.missing_models},
        }

    async def fake_models_status(self, *, required_pipeline: str = "shorts_sdxl"):
        assert required_pipeline == "shorts_sdxl"
        return {
            "ready": True,
            "pipelines": {"shorts_sdxl": {"ready": True, "missing": []}},
            "vram": {"free_gb": 16},
            "auto_download_endpoint": "POST /api/models/ensure-pipeline/shorts_full?wait=true",
        }

    with patch(
        "atnos.services.production_runtime_preparation_service.OllamaModelRolesService.probe",
        fake_probe,
    ), patch(
        "atnos.services.production_runtime_preparation_service.OllamaModelRolesService.pull_missing_models",
        fake_pull_missing,
    ), patch(
        "atnos.services.production_runtime_preparation_service.ProductionRuntimePreparationService._prepare_comfyui",
        return_value=ComfyUiRuntimeReadiness(image_generation=True, detail="ready"),
    ), patch(
        "atnos.services.production_runtime_preparation_service.ProductionRuntimePreparationService._models_status",
        fake_models_status,
    ), patch(
        "atnos.services.production_runtime_preparation_service.ShortProductionReadinessService.check",
        _fake_readiness_check_with_publish_missing,
    ):
        result = __import__("asyncio").run(service.prepare(ensure_ollama_models=True))

    payload = result.to_dict()
    assert pull_calls == [("qwen3:14b",)]
    assert payload["status"] == "local_runtime_ready"
    assert payload["local_runtime_ready"] is True
    assert payload["publish_ready"] is False
    assert payload["full_pipeline_ready"] is False
    assert payload["ollama"]["ready"] is True
    assert payload["ollama_models"]["requested"] is True
    assert payload["ollama_models"]["status"] == "ready"


def test_prepare_runtime_endpoint_returns_service_payload(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    async def fake_prepare(
        self,
        *,
        start_comfyui: bool = True,
        comfyui_wait_timeout_s: int = 30,
        ensure_models: bool = False,
        ensure_ollama_models: bool = False,
        model_pipeline: str = "shorts_sdxl",
    ):
        assert start_comfyui is False
        assert comfyui_wait_timeout_s == 7
        assert ensure_models is True
        assert ensure_ollama_models is True
        assert model_pipeline == "shorts_full"

        class _Result:
            @staticmethod
            def to_dict():
                return {
                    "status": "needs_action",
                    "local_runtime_ready": False,
                    "publish_ready": False,
                    "full_pipeline_ready": False,
                    "ollama": {"ready": False},
                    "ollama_models": {"requested": True},
                    "comfyui": {"image_generation": False},
                    "models": {"pipelines": {"shorts_full": {"ready": False}}},
                    "readiness": {"ready": False},
                    "next_actions": ["ollama pull qwen3:14b"],
                }

        return _Result()

    with patch(
        "atnos.api.routes.production.ProductionRuntimePreparationService.prepare",
        fake_prepare,
    ):
        with TestClient(app) as client:
            response = client.post(
                "/api/production/runtime/prepare",
                json={
                    "start_comfyui": False,
                    "comfyui_wait_timeout_s": 7,
                    "ensure_models": True,
                    "ensure_ollama_models": True,
                    "model_pipeline": "shorts_full",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_action"
    assert payload["local_runtime_ready"] is False
    assert payload["publish_ready"] is False
    assert payload["full_pipeline_ready"] is False
    assert payload["ollama_models"]["requested"] is True
    assert "models" in payload
    assert payload["next_actions"] == ["ollama pull qwen3:14b"]


def test_prepare_runtime_endpoint_defaults_to_non_downloading_sdxl_probe(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    calls: list[dict[str, object]] = []

    async def fake_prepare(
        self,
        *,
        start_comfyui: bool = True,
        comfyui_wait_timeout_s: int = 30,
        ensure_models: bool = False,
        ensure_ollama_models: bool = False,
        model_pipeline: str = "shorts_sdxl",
    ):
        calls.append(
            {
                "start_comfyui": start_comfyui,
                "comfyui_wait_timeout_s": comfyui_wait_timeout_s,
                "ensure_models": ensure_models,
                "ensure_ollama_models": ensure_ollama_models,
                "model_pipeline": model_pipeline,
            }
        )

        class _Result:
            @staticmethod
            def to_dict():
                return {
                    "status": "needs_action",
                    "local_runtime_ready": False,
                    "publish_ready": False,
                    "full_pipeline_ready": False,
                    "ollama": {"ready": False},
                    "ollama_models": {"requested": True},
                    "comfyui": {"image_generation": False},
                    "models": {"auto_download": {"requested": True}},
                    "readiness": {"ready": False},
                    "next_actions": [],
                }

        return _Result()

    with patch(
        "atnos.api.routes.production.ProductionRuntimePreparationService.prepare",
        fake_prepare,
    ):
        with TestClient(app) as client:
            response = client.post("/api/production/runtime/prepare", json={})

    assert response.status_code == 200
    assert calls == [
        {
            "start_comfyui": True,
            "comfyui_wait_timeout_s": 180,
            "ensure_models": False,
            "ensure_ollama_models": False,
            "model_pipeline": "shorts_sdxl",
        }
    ]


def test_prepare_runtime_endpoint_accepts_missing_body(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    calls: list[dict[str, object]] = []

    async def fake_prepare(
        self,
        *,
        start_comfyui: bool = True,
        comfyui_wait_timeout_s: int = 30,
        ensure_models: bool = False,
        ensure_ollama_models: bool = False,
        model_pipeline: str = "shorts_sdxl",
    ):
        calls.append(
            {
                "start_comfyui": start_comfyui,
                "comfyui_wait_timeout_s": comfyui_wait_timeout_s,
                "ensure_models": ensure_models,
                "ensure_ollama_models": ensure_ollama_models,
                "model_pipeline": model_pipeline,
            }
        )

        class _Result:
            @staticmethod
            def to_dict():
                return {
                    "status": "needs_action",
                    "local_runtime_ready": False,
                    "publish_ready": False,
                    "full_pipeline_ready": False,
                    "ollama": {"ready": False},
                    "ollama_models": {"requested": True},
                    "comfyui": {"image_generation": False},
                    "models": {"auto_download": {"requested": True}},
                    "readiness": {"ready": False},
                    "next_actions": [],
                }

        return _Result()

    with patch(
        "atnos.api.routes.production.ProductionRuntimePreparationService.prepare",
        fake_prepare,
    ):
        with TestClient(app) as client:
            response = client.post("/api/production/runtime/prepare")

    assert response.status_code == 200
    assert calls == [
        {
            "start_comfyui": True,
            "comfyui_wait_timeout_s": 180,
            "ensure_models": False,
            "ensure_ollama_models": False,
            "model_pipeline": "shorts_sdxl",
        }
    ]
