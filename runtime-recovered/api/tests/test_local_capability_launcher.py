from __future__ import annotations

from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest


APP_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER_PATH = APP_ROOT / "Launch-Atnos.ps1"
SHELL_SOURCE_PATH = APP_ROOT / "desktop-shell" / "AtnosShell.cs"


class LocalCapabilityLauncherContractTest(unittest.TestCase):
    def test_launcher_generates_and_passes_ephemeral_capability(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="utf-8")

        self.assertIn("RandomNumberGenerator", source)
        self.assertIn("Add-Type -AssemblyName System.Security", source)
        self.assertIn("ProtectedData]::Protect", source)
        self.assertIn("ProtectedData]::Unprotect", source)
        self.assertNotIn("ConvertTo-SecureString", source)
        self.assertNotIn("ConvertFrom-SecureString", source)
        self.assertIn("$env:ATNOS_LOCAL_CAPABILITY_TOKEN = $token", source)
        self.assertIn("$env:ATNOS_LOCAL_CAPABILITY_EXPIRES_AT", source)
        self.assertIn('"X-Atnos-Local-Capability"', source)
        self.assertIn("/local-host/capability-session", source)
        self.assertNotIn("#atnos_local_capability=", source)
        self.assertIn("ATNOS_LOCAL_CAPABILITY_SESSION_FILE", source)
        self.assertIn("$runtimeLogRoot", source)

    def test_capability_is_not_exposed_in_desktop_process_arguments(self) -> None:
        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8")
        shell_source = SHELL_SOURCE_PATH.read_text(encoding="utf-8")

        open_window = launcher_source.split("function Open-AtnosWindow", 1)[1].split(
            "function Ensure-RuntimeWatchdog", 1
        )[0]
        self.assertNotIn("atnos_local_capability=", open_window)
        self.assertIn("Remove-Item Env:ATNOS_LOCAL_CAPABILITY_TOKEN", open_window)
        self.assertIn("ProtectedData.Unprotect", shell_source)
        self.assertIn("AddScriptToExecuteOnDocumentCreatedAsync", shell_source)
        self.assertIn("atnos.localCapabilitySession.v1", shell_source)

    def test_managed_shell_is_signed_and_trust_checked_before_launch(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="utf-8")

        self.assertIn("CN=Atnos Local Build", source)
        self.assertIn("Set-AuthenticodeSignature", source)
        self.assertIn("function Test-ManagedDesktopShellTrusted", source)
        self.assertIn("if (Test-ManagedDesktopShellTrusted)", source)

    def test_launcher_does_not_contain_a_static_capability_value(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="utf-8")

        hardcoded_assignment = re.compile(
            r"ATNOS_LOCAL_CAPABILITY_TOKEN\s*=\s*['\"][^$][^'\"]+['\"]",
            re.IGNORECASE,
        )
        self.assertIsNone(hardcoded_assignment.search(source))

    def test_launcher_can_replace_the_capability_session_on_restart(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="utf-8")

        self.assertIn("[System.IO.FileMode]::Create", source)
        self.assertIn("[System.IO.FileShare]::None", source)
        self.assertIn("$sessionStream.Flush($true)", source)
        self.assertIn('"$env:USERNAME`:(M)"', source)
        self.assertNotIn("[System.IO.File]::Move($temporaryFile", source)
        self.assertNotIn("Move-Item -LiteralPath $temporaryFile", source)

    def test_repeated_launch_uses_local_mutex_and_single_shell_activation(self) -> None:
        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8")
        shell_source = SHELL_SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn('"Local\\AtnosLauncherV2"', launcher_source)
        self.assertNotIn('"Global\\AtnosLauncherF"', launcher_source)

        open_window = launcher_source.split("function Open-AtnosWindow", 1)[1].split(
            "function Ensure-RuntimeWatchdog", 1
        )[0]
        self.assertNotIn("Stop-ManagedDesktopShellProcesses", open_window)

        self.assertIn('SingleInstanceMutexName = @"Local\\AtnosDesktopShellV2"', shell_source)
        self.assertIn('ActivationEventName = @"Local\\AtnosDesktopShellActivateV2"', shell_source)
        self.assertIn("SignalExistingInstance();", shell_source)
        self.assertIn("shellForm.RestoreAndActivate", shell_source)

    def test_launcher_deploys_current_frontend_before_starting_web_shell(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="utf-8")

        self.assertIn("function Ensure-FrontendDeployment", source)
        self.assertIn("deploy_frontend_to_desktop_web.ps1", source)
        self.assertIn("Get-AtnosFileSha256 -LiteralPath $distIndex", source)
        self.assertIn("& $frontendDeployScript -RepoRoot $appRoot -Build", source)
        # Services-only recovery intentionally does not rebuild/deploy the UI.
        # A normal launch must still deploy before starting or reusing the web shell.
        startup = source.split('Write-LauncherLog "Launcher started."', 1)[1]
        deployment = "if (-not $ServicesOnly) { [void](Ensure-FrontendDeployment) }"
        self.assertIn(deployment, startup)
        self.assertLess(startup.index(deployment), startup.index("    Ensure-Web"))

    def test_launcher_checks_source_freshness_before_fast_health_reuse(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        ensure_api = source.split("function Ensure-Api", 1)[1].split(
            "function Ensure-OllamaRuntime", 1
        )[0]
        ensure_web = source.split("function Ensure-Web", 1)[1].split(
            "function Ensure-UiBootstrap", 1
        )[0]

        self.assertLess(
            ensure_api.index("Test-ServiceStale"),
            ensure_api.index("Test-ServiceHealthyFast"),
        )
        self.assertLess(
            ensure_web.index("Test-ServiceStale"),
            ensure_web.index("Test-ServiceHealthyFast"),
        )

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell required")
    def test_frontend_hash_runs_without_module_autoloading(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        function = "function Get-AtnosFileSha256" + source.split(
            "function Get-AtnosFileSha256", 1
        )[1].split("function Ensure-FrontendDeployment", 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "index [desktop].html"
            for content in (b"first build", b"changed build"):
                path.write_bytes(content)
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     "$ErrorActionPreference='Stop'; $PSModuleAutoLoadingPreference='None';\n"
                     + function + "\nGet-AtnosFileSha256 -LiteralPath $env:ATNOS_TEST_HASH_FILE"],
                    env={**os.environ, "ATNOS_TEST_HASH_FILE": str(path)},
                    capture_output=True, text=True, timeout=20, check=True,
                )
                self.assertEqual(result.stdout.strip().lower(), hashlib.sha256(content).hexdigest())

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell required")
    def test_frontend_hash_rejects_missing_files(self) -> None:
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        function = "function Get-AtnosFileSha256" + source.split(
            "function Get-AtnosFileSha256", 1
        )[1].split("function Ensure-FrontendDeployment", 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "$ErrorActionPreference='Stop';\n" + function
                 + "\nGet-AtnosFileSha256 -LiteralPath $env:ATNOS_TEST_HASH_FILE"],
                env={**os.environ, "ATNOS_TEST_HASH_FILE": str(Path(folder) / "missing.html")},
                capture_output=True, text=True, timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
