from pathlib import Path
import subprocess


def test_launcher_preserves_configured_model_roles_without_importing_secrets(tmp_path):
    root = Path(__file__).resolve().parents[3]
    fixture = Path(__file__).parent/'fixtures/ollama_model_roles.ps1'
    result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(fixture),
                             '-appRoot',str(root),'-testRoot',str(tmp_path)],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS: configured model roles' in result.stdout


def test_desktop_launch_and_recovery_preserve_models_and_foreign_processes():
    root = Path(__file__).resolve().parents[3]
    fixture = Path(__file__).parent / 'fixtures/runtime_launcher_safety.ps1'
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(fixture), '-appRoot', str(root)],
        capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('PASS:') == 6


def test_configured_ollama_port_isolated_from_foreign_runtime(tmp_path):
    root = Path(__file__).resolve().parents[3]
    fixture = Path(__file__).parent / 'fixtures/ollama_configured_port.ps1'
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(fixture),
         '-appRoot', str(root), '-testRoot', str(tmp_path)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('PASS:') == 7
