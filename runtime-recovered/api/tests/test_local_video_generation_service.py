from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from atnos.services.local_video_generation_service import (
    LocalVideoGenerationService,
    VideoGenerationError,
    VideoGenerationReadiness,
)

from .test_support import build_test_settings, make_temp_dir


def test_startup_ready_does_not_install_or_download(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        service = LocalVideoGenerationService(build_test_settings(Path(temp_dir)))
        monkeypatch.setattr(service, "ensure_dependencies", lambda: (_ for _ in ()).throw(AssertionError()))
        monkeypatch.setattr(service, "ensure_model_ready", lambda backend: (_ for _ in ()).throw(AssertionError()))
        monkeypatch.setattr(service, "missing_dependencies", lambda: ())

        readiness = service.ensure_startup_ready()

        assert readiness.video_generation is False
        assert readiness.reason == "Video generation model is not downloaded"
        assert readiness.dependencies_installed is True


def test_startup_ready_reports_missing_dependencies_without_installing(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        service = LocalVideoGenerationService(build_test_settings(Path(temp_dir)))
        monkeypatch.setattr(service, "ensure_dependencies", lambda: (_ for _ in ()).throw(AssertionError()))
        monkeypatch.setattr(service, "ensure_model_ready", lambda backend: (_ for _ in ()).throw(AssertionError()))
        monkeypatch.setattr(service, "missing_dependencies", lambda: ("torch", "diffusers"))

        readiness = service.ensure_startup_ready()

        assert readiness.video_generation is False
        assert readiness.reason == "Video generation dependencies are not installed"
        assert readiness.dependencies_installed is False
        assert "torch, diffusers" in (readiness.detail or "")


def test_startup_ready_detects_local_model_without_download(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        settings = build_test_settings(Path(temp_dir))
        service = LocalVideoGenerationService(settings)
        model_dir = service._model_dir("ltx-video")
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model_index.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(service, "ensure_dependencies", lambda: (_ for _ in ()).throw(AssertionError()))
        monkeypatch.setattr(service, "ensure_model_ready", lambda backend: (_ for _ in ()).throw(AssertionError()))
        monkeypatch.setattr(service, "missing_dependencies", lambda: ())

        readiness = service.ensure_startup_ready()

        assert readiness.video_generation is True
        assert readiness.backend == "ltx-video"
        assert readiness.model_dir == model_dir
        assert readiness.dependencies_installed is True
        assert readiness.model_downloaded is False


def test_generation_falls_back_from_ltx_to_wan(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        settings = build_test_settings(root)
        service = LocalVideoGenerationService(settings)
        output_path = settings.data_dir / "atnos-outputs" / "global" / "video.mp4"
        attempts: list[str] = []
        monkeypatch.setattr(service, "ensure_dependencies", lambda: False)
        monkeypatch.setattr(service, "_vram_total_gb", lambda: 16.0)
        monkeypatch.setattr(
            service,
            "ensure_model_ready",
            lambda backend: VideoGenerationReadiness(
                video_generation=True,
                backend=backend,
                model_dir=settings.data_dir / "models" / "video" / backend,
            ),
        )
        monkeypatch.setattr(service, "_verify_mp4", lambda path: path.exists() and path.stat().st_size > 0)

        def fake_generate(**kwargs):
            backend = kwargs["backend"]
            attempts.append(backend)
            if backend == "ltx-video":
                raise RuntimeError("ltx failed")
            kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
            kwargs["output_path"].write_bytes(b"fake-mp4")

        monkeypatch.setattr(service, "_generate_with_backend", fake_generate)

        result = service.generate_video(
            prompt="Atnos product promo",
            output_path=output_path,
            duration_sec=8,
            fps=24,
            width=512,
            height=512,
        )

        assert result.backend == "wan-video"
        assert result.output_path == output_path.resolve()
        assert attempts == ["ltx-video", "wan-video"]


def test_generation_downscales_when_vram_is_below_8gb(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        settings = build_test_settings(root)
        service = LocalVideoGenerationService(settings)
        output_path = settings.data_dir / "atnos-outputs" / "global" / "video.mp4"
        captured: dict[str, object] = {}
        monkeypatch.setattr(service, "ensure_dependencies", lambda: False)
        monkeypatch.setattr(service, "_vram_total_gb", lambda: 7.5)
        monkeypatch.setattr(
            service,
            "ensure_model_ready",
            lambda backend: VideoGenerationReadiness(
                video_generation=True,
                backend=backend,
                model_dir=settings.data_dir / "models" / "video" / backend,
            ),
        )
        monkeypatch.setattr(service, "_verify_mp4", lambda path: True)

        def fake_generate(**kwargs):
            captured.update(kwargs)
            kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
            kwargs["output_path"].write_bytes(b"fake-mp4")

        monkeypatch.setattr(service, "_generate_with_backend", fake_generate)

        result = service.generate_video(
            prompt="Atnos product promo",
            output_path=output_path,
            duration_sec=10,
            fps=24,
            width=768,
            height=768,
        )

        assert result.width == 256
        assert result.height == 256
        assert result.duration_sec == 4
        assert captured["frame_count"] == 96


def test_dependency_install_raises_clear_error(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        service = LocalVideoGenerationService(build_test_settings(Path(temp_dir)))
        monkeypatch.setattr(
            "atnos.services.local_video_generation_service.importlib.util.find_spec",
            lambda package: None,
        )
        monkeypatch.setattr(
            "atnos.services.local_video_generation_service.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="pip failed", stdout=""),
        )

        try:
            service.ensure_dependencies()
        except VideoGenerationError as exc:
            assert "dependency install failed" in str(exc)
        else:
            raise AssertionError("Expected dependency install failure")
