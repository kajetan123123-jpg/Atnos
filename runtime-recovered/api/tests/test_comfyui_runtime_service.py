from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

from .test_support import build_test_settings, make_temp_dir


def _create_portable_install(root: Path) -> None:
    install_dir = root / "ComfyUI_windows_portable"
    (install_dir / "python_embeded").mkdir(parents=True)
    (install_dir / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True)
    (install_dir / "python_embeded" / "python.exe").write_text("", encoding="utf-8")
    (install_dir / "ComfyUI" / "main.py").write_text("print('comfy')", encoding="utf-8")


def _create_desktop_install(root: Path) -> Path:
    install_dir = root / "ComfyDesktop" / "Atnos"
    (install_dir / "ComfyUI" / ".venv" / "Scripts").mkdir(parents=True)
    (install_dir / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True)
    (install_dir / "ComfyUI" / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (install_dir / "ComfyUI" / "main.py").write_text("print('desktop comfy')", encoding="utf-8")
    (install_dir / ".comfyui-desktop-2").write_text("managed", encoding="utf-8")
    return install_dir


def test_desktop_dependency_venv_precedes_standalone_base_python(tmp_path):
    install = _create_desktop_install(tmp_path)
    (install / 'standalone-env').mkdir()
    (install / 'standalone-env/python.exe').write_text('base python without torch')
    settings = build_test_settings(tmp_path)
    settings.comfyui_install_dir = install
    service = ComfyUiRuntimeService(settings)
    assert service.describe_install().python_executable == (install / 'ComfyUI/.venv/Scripts/python.exe').resolve()


def test_portable_embedded_python_remains_first_choice(tmp_path):
    _create_portable_install(tmp_path)
    install = tmp_path / 'ComfyUI_windows_portable'
    (install / 'ComfyUI/.venv/Scripts').mkdir(parents=True)
    (install / 'ComfyUI/.venv/Scripts/python.exe').write_text('secondary')
    service = ComfyUiRuntimeService(build_test_settings(tmp_path))
    assert service.describe_install().python_executable == (install / 'python_embeded/python.exe').resolve()


def test_startup_ready_reports_existing_comfyui_runtime(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiRuntimeService(build_test_settings(root))
        monkeypatch.setattr(service, "_server_ready", lambda: True)

        readiness = service.ensure_startup_ready(wait_timeout_s=30)

        assert readiness.image_generation is True
        assert readiness.reason is None
        assert readiness.started is False
        assert readiness.install_dir is not None
        assert readiness.main_script is not None
        assert readiness.python_executable is not None
        assert readiness.checkpoint_dir is not None


def test_server_discovery_follows_comfy_desktop_port_drift(monkeypatch) -> None:
    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    requested: list[str] = []

    class Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> Response:
            requested.append(url)
            if url == "http://127.0.0.1:8189/system_stats":
                return Response(200)
            raise ConnectionError(url)

    with make_temp_dir() as temp_dir:
        settings = build_test_settings(Path(temp_dir))
        settings.comfyui_base_url = "http://127.0.0.1:8188"
        service = ComfyUiRuntimeService(settings)
        monkeypatch.setattr("atnos.services.comfyui_runtime_service.httpx.Client", Client)

        assert service._server_ready() is True
        assert service.base_url == "http://127.0.0.1:8189"
        assert settings.comfyui_base_url == "http://127.0.0.1:8189"
        assert requested[:2] == [
            "http://127.0.0.1:8188/system_stats",
            "http://127.0.0.1:8189/system_stats",
        ]


def test_startup_ready_auto_launches_configured_comfyui(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiRuntimeService(build_test_settings(root))
        launches: list[bool] = []
        monkeypatch.setattr(service, "_server_ready", lambda: False)
        monkeypatch.setattr(
            service,
            "_launch_from_state",
            lambda state, *, lowvram, disable_smart_memory: launches.append(lowvram) or True,
        )
        monkeypatch.setattr(service, "_wait_until_ready", lambda *, wait_timeout_s: True)

        readiness = service.ensure_startup_ready(wait_timeout_s=30)

        assert readiness.image_generation is True
        assert readiness.started is True
        assert readiness.install_dir is not None
        assert readiness.main_script is not None
        assert launches == [bool(service.settings.comfyui_lowvram)]


def test_startup_ready_records_failure_without_crashing(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiRuntimeService(build_test_settings(root))
        monkeypatch.setattr(service, "_server_ready", lambda: False)
        monkeypatch.setattr(service, "_launch_from_state", lambda state, *, lowvram, disable_smart_memory: True)
        monkeypatch.setattr(service, "_wait_until_ready", lambda *, wait_timeout_s: False)
        monkeypatch.setattr(service, "error_log_tail", lambda: "comfy startup traceback")

        readiness = service.ensure_startup_ready(wait_timeout_s=30)

        assert readiness.image_generation is False
        assert readiness.reason == "ComfyUI startup failed"
        assert readiness.detail == "comfy startup traceback"
        assert readiness.log_tail == "comfy startup traceback"
        assert readiness.install_dir is not None
        assert readiness.main_script is not None


def test_startup_ready_quarantines_known_blocking_wav2lip_node_and_retries(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        install_dir = root / "ComfyUI_windows_portable"
        blocking_node = install_dir / "ComfyUI" / "custom_nodes" / "ComfyUI_wav2lip"
        blocking_node.mkdir(parents=True)
        (blocking_node / "marker.txt").write_text("keep me", encoding="utf-8")
        settings = build_test_settings(root)
        settings.quarantine_dir = root / "quarantine"
        service = ComfyUiRuntimeService(settings)
        launches: list[bool] = []
        waits = iter([False, True])
        log_tail = (
            "Base directory: ComfyUI_wav2lip\n"
            "Wav2Lip path added to sys.path\n"
            "[WARNING] offload-arch failed with return code 1"
        )

        monkeypatch.setattr(service, "_server_ready", lambda: False)
        monkeypatch.setattr(
            service,
            "_launch_from_state",
            lambda state, *, lowvram, disable_smart_memory: launches.append(disable_smart_memory) or True,
        )
        monkeypatch.setattr(service, "_wait_until_ready", lambda *, wait_timeout_s: next(waits))
        monkeypatch.setattr(service, "_terminate_runtime_processes", lambda: True)
        monkeypatch.setattr(service, "error_log_tail", lambda: log_tail)

        readiness = service.ensure_startup_ready(wait_timeout_s=30)

        assert readiness.image_generation is True
        assert readiness.detail == "ComfyUI started after quarantining blocking custom node(s)"
        assert readiness.quarantined_custom_nodes
        assert not blocking_node.exists()
        quarantined_dirs = list((settings.quarantine_dir / "comfyui_custom_nodes").glob("ComfyUI_wav2lip-*"))
        assert len(quarantined_dirs) == 1
        assert (quarantined_dirs[0] / "marker.txt").read_text(encoding="utf-8") == "keep me"
        assert launches == [False, False]


def test_restart_server_passes_lowvram_to_relaunch(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiRuntimeService(build_test_settings(root))
        lowvram_values: list[bool] = []
        monkeypatch.setattr(service, "_terminate_runtime_processes", lambda: True)
        monkeypatch.setattr(
            service,
            "ensure_server_running",
            lambda *, wait_timeout_s, lowvram: lowvram_values.append(lowvram) or True,
        )

        assert service.restart_server(wait_timeout_s=30, lowvram=True) is True
        assert lowvram_values == [True]


def test_restart_server_does_not_kill_desktop_managed_instance(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        install_dir = root / "ComfyDesktop" / "Atnos"
        (install_dir / "ComfyUI").mkdir(parents=True)
        (install_dir / "ComfyUI" / "main.py").write_text("print('desktop managed')", encoding="utf-8")
        settings = build_test_settings(root)
        settings.comfyui_install_dir = install_dir
        service = ComfyUiRuntimeService(settings)
        terminated: list[bool] = []
        monkeypatch.setattr(service, "_terminate_runtime_processes", lambda: terminated.append(True))

        assert service.restart_server(wait_timeout_s=30, lowvram=True) is False
        assert terminated == []


def test_describe_install_detects_desktop_venv_without_auto_launching_it() -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        install_dir = _create_desktop_install(root)
        settings = build_test_settings(root)
        settings.comfyui_install_dir = install_dir

        state = ComfyUiRuntimeService(settings).describe_install()

        assert state.python_executable == (install_dir / "ComfyUI" / ".venv" / "Scripts" / "python.exe").resolve()
        assert ComfyUiRuntimeService(settings).auto_launch_supported(state) is False


def test_startup_ready_does_not_bypass_comfy_desktop_manager(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        install_dir = _create_desktop_install(root)
        settings = build_test_settings(root)
        settings.comfyui_install_dir = install_dir
        service = ComfyUiRuntimeService(settings)
        launches: list[bool] = []
        monkeypatch.setattr(service, "_server_ready", lambda: False)
        monkeypatch.setattr(
            service,
            "_launch_from_state",
            lambda state, *, lowvram, disable_smart_memory: launches.append(True) or True,
        )

        readiness = service.ensure_startup_ready(wait_timeout_s=30)

        assert readiness.image_generation is False
        assert readiness.reason == "Comfy Desktop instance is offline"
        assert launches == []


def test_desktop_managed_launch_requires_explicit_opt_in() -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        install_dir = _create_desktop_install(root)
        settings = build_test_settings(root)
        settings.comfyui_install_dir = install_dir
        service = ComfyUiRuntimeService(settings)
        state = service.describe_install()

        assert service.auto_launch_supported(state) is False

        settings.comfyui_allow_managed_launch = True

        assert service.auto_launch_supported(state) is True


def test_desktop_managed_launch_uses_stable_runtime_arguments(monkeypatch) -> None:
    # Managed ComfyUI must inherit an E:-resident data/profile tree, matching
    # the production storage contract enforced by _launch_python.
    with tempfile.TemporaryDirectory(prefix="atnos-comfy-launch-", dir="E:/Atnos/data/tmp") as temp_dir:
        root = Path(temp_dir)
        install_dir = _create_desktop_install(root)
        shared_paths = root / "shared_model_paths.yaml"
        input_dir = root / "input"
        output_dir = root / "output"
        shared_paths.write_text("atnos: {}", encoding="utf-8")
        input_dir.mkdir()
        output_dir.mkdir()
        settings = build_test_settings(root)
        settings.comfyui_install_dir = install_dir
        settings.comfyui_allow_managed_launch = True
        settings.comfyui_shared_model_paths = shared_paths
        settings.comfyui_input_dir = input_dir
        settings.comfyui_output_dir = output_dir
        settings.comfyui_cache_classic = True
        settings.comfyui_reserve_vram_gb = 1.0
        service = ComfyUiRuntimeService(settings)
        state = service.describe_install()
        calls: list[tuple[list[str], dict[str, object]]] = []

        def capture_popen(args: list[str], **kwargs: object) -> object:
            calls.append((args, kwargs))
            return object()

        monkeypatch.setattr(subprocess, "Popen", capture_popen)

        launched = service._launch_from_state(
            state,
            lowvram=True,
            disable_smart_memory=False,
        )

        assert launched is True
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert "--windows-standalone-build" not in args
        assert "--lowvram" in args
        assert "--disable-async-offload" in args
        assert "--disable-dynamic-vram" in args
        assert "--disable-mmap" in args
        assert "--disable-all-custom-nodes" in args
        assert "--cache-classic" in args
        assert args[args.index("--reserve-vram") + 1] == "1.0"
        assert args[args.index("--extra-model-paths-config") + 1] == str(shared_paths)
        assert args[args.index("--input-directory") + 1] == str(input_dir)
        assert args[args.index("--output-directory") + 1] == str(output_dir)
        assert kwargs["cwd"] == str((install_dir / 'ComfyUI').resolve())
        env = kwargs['env']
        for key in ('USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP',
                    'PYTHONPYCACHEPREFIX', 'HF_HOME', 'XDG_CACHE_HOME', 'TORCH_HOME', 'PIP_CACHE_DIR'):
            assert Path(env[key]).resolve().drive.casefold() == 'e:'
        assert env['TEMP'] == env['TMP']
        assert Path(env['TEMP']).is_dir()
        assert Path(env['HF_HOME']).is_dir()
