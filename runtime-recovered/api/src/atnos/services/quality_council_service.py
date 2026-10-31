from __future__ import annotations

"""Evidence-first quality council for the Atnos runtime.

The council intentionally reports limits of each check.  It never upgrades a
missing browser review, unavailable Gmail session, or absent video artifact to
"passed" merely because the surrounding runtime is online.
"""

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy.orm import Session

from atnos.ai_workers.delegation_router import AiDelegationRouter, DelegateTaskInput
from atnos.core.config import Settings
from atnos.investment.cockpit import InvestmentCockpitService
from atnos.services.gmail_connector_service import GmailConnectorService
from atnos.services.runtime_health_service import RuntimeHealthService


AuditKey = Literal[
    "application_ui",
    "video_quality",
    "investment_strategy",
    "security_permissions",
    "claude_design",
    "kimi_models",
]

DEFAULT_AUDITS: tuple[AuditKey, ...] = (
    "application_ui",
    "video_quality",
    "investment_strategy",
    "security_permissions",
    "claude_design",
    "kimi_models",
)


class QualityCouncilService:
    """Runs inspectable quality checks and writes a separate report per area."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.reports_dir = settings.data_dir / "quality-council"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        *,
        audits: tuple[AuditKey, ...] = DEFAULT_AUDITS,
        include_external_handoffs: bool = True,
        attempt_gmail_delivery: bool = True,
    ) -> dict[str, Any]:
        requested = tuple(dict.fromkeys(audits))
        invalid = sorted(set(requested).difference(DEFAULT_AUDITS))
        if invalid:
            raise ValueError(f"Unknown quality council audits: {', '.join(invalid)}")

        run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        run_dir = self.reports_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        reports: list[dict[str, Any]] = []

        for audit in requested:
            if audit == "application_ui":
                report = self._application_ui_report(run_id, run_dir)
            elif audit == "video_quality":
                report = await asyncio.to_thread(self._video_quality_report, run_id)
            elif audit == "investment_strategy":
                report = self._investment_strategy_report(run_id)
            elif audit == "security_permissions":
                report = self._security_permissions_report(run_id)
            elif audit == "claude_design":
                report = await self._external_handoff_report(
                    run_id,
                    worker_key="browser_claude",
                    title="Atnos — propozycja nowego designu i plan wdrożenia",
                    task_type="style_guide",
                    prompt=_claude_design_prompt(),
                    enabled=include_external_handoffs,
                )
            else:
                report = await self._external_handoff_report(
                    run_id,
                    worker_key="browser_kimi",
                    title="Atnos — rekomendowana konfiguracja modeli AI i LLM",
                    task_type="deep_research",
                    prompt=_kimi_model_prompt(),
                    enabled=include_external_handoffs,
                )
            reports.append(self._write_report(run_dir, report))

        coordinator = self._coordinator_report(run_id, reports)
        coordinator = self._write_report(run_dir, coordinator)
        delivery = self._delivery_status(
            coordinator=coordinator,
            attempt_gmail_delivery=attempt_gmail_delivery,
        )
        manifest = {
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "reports": reports,
            "coordinator": coordinator,
            "delivery": delivery,
            "run_dir": str(run_dir),
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest

    def recent_runs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        manifests = sorted(
            self.reports_dir.glob("*/manifest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        payloads: list[dict[str, Any]] = []
        for path in manifests[: max(0, limit)]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def status(self) -> dict[str, Any]:
        latest = self.recent_runs(limit=1)
        try:
            gmail = GmailConnectorService(self.session, self.settings).get_status()
            gmail_ready = bool(gmail.connected and gmail.live_write_enabled)
            gmail_oauth_status = gmail.oauth_status
            gmail_reason = (
                "Gmail is connected; reports can be drafted for supervised delivery."
                if gmail_ready
                else "Gmail report delivery is blocked until an active Gmail OAuth token with write scope exists."
            )
        except Exception as exc:
            self.session.rollback()
            gmail_ready = False
            gmail_oauth_status = "unavailable"
            gmail_reason = f"Gmail status could not be checked: {type(exc).__name__}: {exc}"
        return {
            "ready": True,
            "reports_dir": str(self.reports_dir),
            "latest_run": latest[0] if latest else None,
            "gmail_delivery": {
                "ready": gmail_ready,
                "oauth_status": gmail_oauth_status,
                "reason": gmail_reason,
            },
        }

    def _application_ui_report(self, run_id: str, run_dir: Path) -> dict[str, Any]:
        health = RuntimeHealthService(self.settings).snapshot(session=self.session)
        components = health.get("components") if isinstance(health.get("components"), list) else []
        failed = [item for item in components if isinstance(item, dict) and item.get("status") in {"missing", "blocked"}]
        degraded = [item for item in components if isinstance(item, dict) and item.get("status") == "degraded"]
        frontend_dir = Path(self.settings.app_root) / "frontend"
        built_index = frontend_dir / "dist" / "index.html"
        source_settings = frontend_dir / "src" / "routes" / "ContentMachineSettings.tsx"
        screenshots = self._capture_ui_snapshots(run_dir)
        evidence = [
            {
                "check": "runtime_health_snapshot",
                "method": "RuntimeHealthService.snapshot(session)",
                "result": health.get("status"),
                "component_count": len(components),
                "blocking_components": [str(item.get("key")) for item in failed],
                "degraded_components": [str(item.get("key")) for item in degraded],
            },
            {
                "check": "desktop_bundle_marker",
                "method": "filesystem existence check",
                "result": "present" if built_index.exists() else "missing",
                "path": str(built_index),
            },
            {
                "check": "settings_surface_marker",
                "method": "filesystem existence check",
                "result": "present" if source_settings.exists() else "missing",
                "path": str(source_settings),
            },
            {
                "check": "headless_ui_snapshots",
                "method": "Chrome/Edge headless screenshot of local desktop shell",
                **screenshots,
            },
        ]
        problems: list[dict[str, Any]] = []
        for component in failed:
            problems.append(
                _problem(
                    severity="high",
                    title=f"Runtime component unavailable: {component.get('label')}",
                    cause=str(component.get("detail") or "No health detail."),
                    fix=str(component.get("next_action") or "Repair the component and rerun the audit."),
                    files=["runtime_health_service.py"],
                    verification="Refresh GET /api/runtime/health and confirm status=ready.",
                    regression_risk="Low; status only changes after the real dependency becomes healthy.",
                )
            )
        if not built_index.exists():
            problems.append(
                _problem(
                    severity="high",
                    title="Desktop frontend bundle is missing",
                    cause="The audit could not find frontend/dist/index.html.",
                    fix="Run the desktop build and restart the desktop shell.",
                    files=["frontend/package.json", "desktop-web"],
                    verification="Confirm frontend/dist/index.html exists and open the Desktop shell.",
                    regression_risk="Medium; build configuration may reveal existing TypeScript errors.",
                )
            )
        return _report(
            run_id=run_id,
            key="application_ui",
            title="Agent testów aplikacji i interfejsu",
            summary=(
                "Wykonano snapshot runtime i statyczne sprawdzenie artefaktu desktopowego. "
                "Nie wykonano jeszcze zautomatyzowanego testu kliknięć, konsoli przeglądarki ani porównania screenshotów."
            ),
            works_well=[
                "Health-check runtime zwraca komponenty i konkretne działania naprawcze.",
                "Artefakt desktopowy i kod ustawień są sprawdzane przez ścieżki lokalne, nie przez atrapę statusu.",
            ],
            works_wrong=[
                f"{len(degraded)} komponent(ów) runtime ma status degraded." if degraded else "Brak komponentów degraded w tym snapshotcie.",
            ],
            not_working=[str(item.get("label")) for item in failed],
            not_verified=[
                "Logowanie i wylogowanie użytkownika.",
                "Klikalne przepływy formularzy, odświeżenie strony oraz wolne połączenie.",
                "Błędy konsoli przeglądarki, modale i pełne przepływy formularzy.",
                *(
                    []
                    if screenshots.get("status") == "captured"
                    else ["Nakładanie elementów, overflow i widok mobilny bez screenshotu."]
                ),
            ],
            evidence=evidence,
            problems=problems,
            confidence="medium",
        )

    def _capture_ui_snapshots(self, run_dir: Path) -> dict[str, Any]:
        if self.settings.app_env == "test":
            return {"status": "skipped", "reason": "Screenshot capture is disabled in the test environment."}
        if os.environ.get("ATNOS_QUALITY_UI_SCREENSHOTS", "").strip().lower() not in {"1", "true", "yes", "on"}:
            return {
                "status": "skipped",
                "reason": (
                    "Headless capture is disabled by default because the background Windows API process cannot "
                    "reliably own a browser session. Run desktop visual QA explicitly with a supervised UI runner."
                ),
            }
        browser = _resolve_headless_browser()
        if browser is None:
            return {"status": "unavailable", "reason": "Chrome or Edge executable was not found."}
        target = "http://127.0.0.1:3000/settings"
        screenshots_dir = run_dir / "ui-snapshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        captures: list[dict[str, Any]] = []
        for label, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
            output = screenshots_dir / f"{label}.png"
            try:
                completed = _run_headless_screenshot(
                    browser=browser,
                    profile_dir=screenshots_dir / f"{label}-profile",
                    output=output,
                    target=target,
                    width=width,
                    height=height,
                )
                capture: dict[str, Any] = {
                    "label": label,
                    "path": str(output),
                    "exit_code": completed.returncode,
                    "exists": output.exists(),
                    "size_bytes": output.stat().st_size if output.exists() else 0,
                }
                if output.exists():
                    try:
                        from PIL import Image

                        with Image.open(output) as image:
                            capture["image_size"] = list(image.size)
                    except Exception as exc:
                        capture["image_size_error"] = f"{type(exc).__name__}: {exc}"
                captures.append(capture)
            except (OSError, subprocess.SubprocessError) as exc:
                captures.append({"label": label, "path": str(output), "error": f"{type(exc).__name__}: {exc}"})
        captured = [item for item in captures if item.get("exists") and int(item.get("size_bytes") or 0) > 1024]
        return {
            "status": "captured" if len(captured) == 2 else "partial" if captured else "failed",
            "target": target,
            "browser": browser,
            "captures": captures,
            "limitation": "Screenshots confirm rendered output and dimensions only; they do not prove absence of all overlap or console errors.",
        }

    def _video_quality_report(self, run_id: str) -> dict[str, Any]:
        output_dir = Path(self.settings.shorts_output_dir)
        files = sorted(output_dir.rglob("*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)[:20] if output_dir.exists() else []
        ffprobe = shutil.which("ffprobe")
        ffmpeg = _resolve_ffmpeg(self.settings)
        items: list[dict[str, Any]] = []
        problems: list[dict[str, Any]] = []
        tiny_files = [path for path in files if path.stat().st_size < 1024]
        invalid_files: list[Path] = []
        for path in files:
            item: dict[str, Any] = {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "probe": "not_available",
            }
            if ffprobe:
                try:
                    completed = subprocess.run(
                        [
                            ffprobe, "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
                            "-of", "json", str(path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                    item["probe"] = "ok" if completed.returncode == 0 else "failed"
                    item["ffprobe"] = json.loads(completed.stdout) if completed.returncode == 0 else completed.stderr[-1000:]
                except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
                    item["probe"] = "failed"
                    item["error"] = f"{type(exc).__name__}: {exc}"
            if ffmpeg:
                try:
                    decode = subprocess.run(
                        [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                    )
                    item["decode"] = "ok" if decode.returncode == 0 else "failed"
                except (OSError, subprocess.SubprocessError) as exc:
                    item["decode"] = "failed"
                    item["decode_error"] = f"{type(exc).__name__}: {exc}"
            if item.get("decode") == "failed" or item.get("probe") == "failed":
                invalid_files.append(path)
            items.append(item)
        if invalid_files:
            problems.append(
                _problem(
                    severity="high",
                    title=f"Technical validation failed for {len(invalid_files)} video output file(s)",
                    cause="One or more render files could not be parsed or decoded by the local validation tool.",
                    fix="Regenerate each listed output and run the production QA gate before upload.",
                    files=[str(path) for path in invalid_files[:10]],
                    verification="ffprobe parses metadata when available and ffmpeg decode exits with code 0 for every regenerated output.",
                    regression_risk="Low; the fix replaces only invalid render artifacts.",
                )
            )
        if tiny_files:
            problems.append(
                _problem(
                    severity="high",
                    title=f"{len(tiny_files)} MP4 output file(s) are too small to be valid renders",
                    cause="Files below 1 KiB are placeholder/truncated artifacts, not reviewable video outputs.",
                    fix="Remove placeholder outputs from the review queue and rerender each affected job through the production QA gate.",
                    files=[str(path) for path in tiny_files[:10]],
                    verification="Each replacement is larger than 1 KiB, ffmpeg decodes it successfully and the production QA report exists.",
                    regression_risk="Low; only invalid output artifacts are replaced.",
                )
            )
        if not ffmpeg:
            problems.append(
                _problem(
                    severity="high",
                    title="FFmpeg executable is unavailable to the video audit",
                    cause="No configured FFmpeg path, PATH command or imageio-ffmpeg fallback was found.",
                    fix="Configure FFMPEG_EXE or install FFmpeg, then rerun the video audit.",
                    files=["runtime_health_service.py", "core/config.py"],
                    verification="Video audit reports ffmpeg_available=true and decodes a known-good MP4.",
                    regression_risk="Low; this changes the validation tool discovery only.",
                )
            )
        return _report(
            run_id=run_id,
            key="video_quality",
            title="Agent kontroli jakości filmów",
            summary=(
                f"Znaleziono {len(files)} plików MP4 w katalogu wyjściowym. "
                "Ocena semantyczna scen, ostrości, napisów i synchronizacji audio wymaga ludzkiego lub vision-review materiału."
            ),
            works_well=[
                "Audyt używa ffprobe/ffmpeg, gdy narzędzia są dostępne, aby potwierdzić metadane i dekodowanie.",
                "Uszkodzone pliki nie są automatycznie uznawane za poprawne.",
            ],
            works_wrong=["Brak automatycznej oceny zgodności obrazu z tematem bez review wizualnego."],
            not_working=[problem["title"] for problem in problems],
            not_verified=(
                ["Brak filmów MP4 w katalogu wyjściowym."]
                if not files
                else [
                    "Ostrość, artefakty kompresji i stabilność kadrowania.",
                    "Synchronizacja narracji z obrazem oraz czytelność napisów.",
                    "Odtwarzanie na telefonie i rzeczywista ocena stylistyczna.",
                ]
            ),
            evidence=[
                {
                    "check": "output_scan",
                    "method": "bounded scan of shorts_output_dir, maximum 20 newest MP4 files",
                    "directory": str(output_dir),
                    "file_count": len(files),
                    "ffprobe_available": bool(ffprobe),
                    "ffmpeg_available": bool(ffmpeg),
                    "items": items,
                }
            ],
            problems=problems,
            confidence="low" if not files or not ffprobe else "medium",
        )

    def _investment_strategy_report(self, run_id: str) -> dict[str, Any]:
        cockpit = InvestmentCockpitService(self.settings)
        summary = cockpit.status()
        policy = cockpit.risk_policy()
        xtb = cockpit.xtb_market_data_status()
        runs = cockpit.list_strategy_runs(limit=50)
        problems: list[dict[str, Any]] = []
        if not bool(xtb.get("read_only_verified")):
            problems.append(
                _problem(
                    severity="high",
                    title="XTB real market-data read-only access is not verified",
                    cause=str(xtb.get("next_action") or "No verified XTB quote snapshot is available."),
                    fix="Bind the encrypted XTB demo credential and manually run read-only verification; never enable live trading.",
                    files=["investment/cockpit.py", "api/routes/investment.py"],
                    verification="GET /api/investment/market-data/xtb-demo/status returns read_only_verified=true.",
                    regression_risk="Medium; broker API differences need an adapter-specific test.",
                )
            )
        if not runs:
            problems.append(
                _problem(
                    severity="high",
                    title="No evidence of backtest, out-of-sample or walk-forward validation",
                    cause="The current paper strategy journal has no run evidence that demonstrates these validations.",
                    fix="Implement a reproducible historical-data evaluation pipeline with transaction costs, benchmark and walk-forward splits before demo recommendations.",
                    files=["investment/cockpit.py", "investment/education_research.py"],
                    verification="Store versioned backtest, out-of-sample and stress-test artifacts in the decision journal.",
                    regression_risk="Medium; historical data assumptions can change existing paper metrics.",
                )
            )
        return _report(
            run_id=run_id,
            key="investment_strategy",
            title="Agent analizy strategii inwestycyjnej",
            summary=(
                "Sprawdzono guardraile paper tradingu, stan market data i dziennik strategii. "
                "To nie jest walidacja rentowności: brak potwierdzonych danych historycznych i niezależnych testów strategii."
            ),
            works_well=[
                "Kod wymusza paper_only i odrzuca próbę włączenia live tradingu.",
                "Polityka ryzyka ma limity pojedynczej transakcji, pozycji oraz strat dziennych i tygodniowych.",
                f"Emergency stop = {bool(policy.get('emergency_stop'))}; real trading enabled = {bool(xtb.get('real_trading_enabled'))}.",
            ],
            works_wrong=[
                "Strategia quote-range jest oznaczona jako paper-only/unvalidated, więc nie ma podstaw do oceny jej przewagi.",
            ],
            not_working=[problem["title"] for problem in problems],
            not_verified=[
                "Backtest z kosztami, spreadem i poślizgiem.",
                "Out-of-sample, walk-forward, stress test i porównanie z benchmarkiem.",
                "Jakość oraz kompletność realnych danych XTB.",
            ],
            evidence=[
                {
                    "check": "investment_cockpit_snapshot",
                    "method": "InvestmentCockpitService.summary/risk_policy/xtb_market_data_status",
                    "mode": summary.get("mode"),
                    "risk_policy": policy,
                    "xtb": xtb,
                    "strategy_run_count": len(runs),
                }
            ],
            problems=problems,
            confidence="medium",
        )

    def _security_permissions_report(self, run_id: str) -> dict[str, Any]:
        root = Path(self.settings.app_root)
        gitignore = root / ".gitignore"
        git_dir = root / ".git"
        ignored = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.exists() else ""
        sensitive_names = (".env", "client_secret.json", "credentials.json", "token.json")
        exposed = [str(root / name) for name in sensitive_names if (root / name).exists()]
        missing_ignore = [name for name in sensitive_names if name not in ignored and f"*{name}" not in ignored]
        health = RuntimeHealthService(self.settings).snapshot(session=self.session)
        controls = next(
            (
                item for item in health.get("components", [])
                if isinstance(item, dict) and item.get("key") == "local_host_control"
            ),
            None,
        )
        router_source = root / "runtime-recovered" / "api" / "src" / "atnos" / "ai_workers" / "delegation_router.py"
        source = router_source.read_text(encoding="utf-8", errors="replace") if router_source.exists() else ""
        guardrails_present = all(token in source for token in ("HIGH_RISK_TOKENS", "redact_text", "redact_payload"))
        problems: list[dict[str, Any]] = []
        if exposed:
            problems.append(
                _problem(
                    severity="critical",
                    title="Sensitive configuration file exists in repository root",
                    cause="A root-level secret-named file can be accidentally copied, committed or exposed by tooling.",
                    fix="Move the secret outside the repository, rotate it if it was ever shared, and retain only a documented local reference.",
                    files=exposed,
                    verification="The root no longer contains the file and git status does not show it.",
                    regression_risk="Medium; OAuth runtime must be reconfigured after relocation.",
                )
            )
        if missing_ignore:
            problems.append(
                _problem(
                    severity="high",
                    title="Secret file patterns are not fully ignored",
                    cause=f"Missing .gitignore patterns: {', '.join(missing_ignore)}.",
                    fix="Add explicit ignore rules for secrets, runtime caches and generated media before any Git initialization.",
                    files=[str(gitignore)],
                    verification="Run git check-ignore for representative secret and output filenames.",
                    regression_risk="Low; ignore rules do not change runtime behavior.",
                )
            )
        if not git_dir.exists():
            problems.append(
                _problem(
                    severity="medium",
                    title="Project root is not an initialized Git worktree",
                    cause="Without a repository, ignore rules cannot be verified and there is no auditable change history.",
                    fix="Create a clean Git repository only after moving or rotating root-level secrets; then verify .gitignore before the first commit.",
                    files=[str(root / ".gitignore")],
                    verification="git rev-parse --is-inside-work-tree returns true and git check-ignore confirms secret/output patterns.",
                    regression_risk="Low; repository initialization does not change application runtime behavior.",
                )
            )
        if not guardrails_present:
            problems.append(
                _problem(
                    severity="high",
                    title="AI delegation redaction or high-risk block could not be statically confirmed",
                    cause="Expected guardrail markers were absent from the delegation router source.",
                    fix="Restore redaction and high-risk task blocking, then add route tests.",
                    files=[str(router_source)],
                    verification="Run ai-worker tests for secret redaction and high-risk action blocking.",
                    regression_risk="High; guardrail changes affect task routing.",
                )
            )
        return _report(
            run_id=run_id,
            key="security_permissions",
            title="Agent bezpieczeństwa, uprawnień i kodu",
            summary=(
                "Wykonano bezpieczny statyczny przegląd konfiguracji, źródeł guardrail i snapshotu local host. "
                "Nie wykonano destrukcyjnych testów, pełnego SAST/DAST ani skanu podatności zależności."
            ),
            works_well=[
                "Delegacja AI posiada redakcję promptów oraz blokadę wysokiego ryzyka, jeśli markery kodu są obecne.",
                "Runtime health rozróżnia gotowość desktop input od aktywności wykonania.",
            ],
            works_wrong=[
                f"{len(exposed)} plik(ów) o nazwach wskazujących na sekret znaleziono w root repo." if exposed else "Nie znaleziono wskazanych nazw sekretów w root repo.",
            ],
            not_working=[problem["title"] for problem in problems],
            not_verified=[
                "Pełny threat model i wieloprzebiegowy security scan.",
                "Skan CVE zależności oraz testy dynamiczne XSS/CSRF/SSRF/injection.",
                "Zakres tokenów zewnętrznych OAuth i faktyczne uprawnienia usług po stronie dostawców.",
            ],
            evidence=[
                {
                    "check": "secret_name_root_scan",
                    "method": "file-name existence check; contents were not read",
                    "exposed_paths": exposed,
                    "missing_gitignore_patterns": missing_ignore,
                    "git_worktree_present": git_dir.exists(),
                },
                {
                    "check": "ai_delegation_guardrails",
                    "method": "static source marker check",
                    "router_source": str(router_source),
                    "guardrails_present": guardrails_present,
                },
                {
                    "check": "local_host_permission_snapshot",
                    "method": "RuntimeHealthService.snapshot(session)",
                    "component": controls,
                },
            ],
            problems=problems,
            confidence="medium",
        )

    async def _external_handoff_report(
        self,
        run_id: str,
        *,
        worker_key: str,
        title: str,
        task_type: str,
        prompt: str,
        enabled: bool,
    ) -> dict[str, Any]:
        handoff: dict[str, Any] | None = None
        if enabled:
            try:
                router = AiDelegationRouter(self.session, self.settings)
                handoff = await router.delegate(
                    DelegateTaskInput(
                        task_type=task_type,
                        prompt=prompt,
                        context={
                            "scope": "Atnos desktop app; reports must distinguish observed facts from assumptions.",
                            "restrictions": "Provide advice only. Do not propose actions that change external accounts or affect money.",
                        },
                        preferred_worker=worker_key,
                        allowed_workers=(worker_key,),
                        privacy_mode="allow_browser_ai",
                        priority="normal",
                        requested_by="quality_council",
                        requires_approval_for_result=True,
                    )
                )
            except Exception as exc:
                # The audit must survive an incomplete migration or unavailable AI queue.
                self.session.rollback()
                handoff = {
                    "status": "blocked",
                    "blocked_reason": f"{type(exc).__name__}: {exc}",
                }
        state = str((handoff or {}).get("status") or "not_requested")
        prepared = state in {"completed", "awaiting_review"}
        return _report(
            run_id=run_id,
            key="claude_design" if worker_key == "browser_claude" else "kimi_models",
            title=title,
            summary=(
                "Przygotowano kontrolowany handoff do aplikacji zewnętrznej. "
                "Atnos nie wpisał promptu ani nie odebrał odpowiedzi automatycznie; wynik wymaga ręcznego uruchomienia i wklejenia odpowiedzi do Atnosa."
                if prepared
                else "Handoff nie został przygotowany, ponieważ worker browser jest zablokowany lub niedostępny."
            ),
            works_well=["Prompt jest redagowany przez warstwę delegacji i nie zawiera sekretów ani akcji wysokiego ryzyka."],
            works_wrong=[],
            not_working=[] if prepared else ["Supervised browser handoff"],
            not_verified=["Odpowiedź zewnętrznego modelu nie została jeszcze odebrana ani zweryfikowana."],
            evidence=[
                {
                    "check": "controlled_browser_handoff",
                    "method": "AiDelegationRouter.delegate",
                    "worker_key": worker_key,
                    "requested": enabled,
                    "handoff": handoff,
                }
            ],
            problems=(
                []
                if prepared
                else [
                    _problem(
                        severity="medium",
                        title=f"{worker_key} handoff is not ready",
                        cause=str((handoff or {}).get("blocked_reason") or "External handoff was disabled."),
                        fix="Enable controlled browser AI in Runtime settings, then rerun the handoff. Do not submit credentials or consent flows.",
                        files=["ai_workers/browser_ai_worker.py", "ai_workers/delegation_router.py"],
                        verification="GET /api/ai-workers/probe/{worker_key} reports ready and the task has a handoff package.",
                        regression_risk="Low; the worker remains supervised-only.",
                    )
                ]
            ),
            confidence="low",
        )

    def _coordinator_report(self, run_id: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
        all_problems = [problem for report in reports for problem in report["problems"]]
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for problem in all_problems:
            key = str(problem["title"]).lower()
            if key not in seen:
                seen.add(key)
                deduped.append(problem)
        critical = sum(item["severity"] == "critical" for item in deduped)
        high = sum(item["severity"] == "high" for item in deduped)
        ratings = {
            "stabilność aplikacji": _rating(None, "Brak pełnego testu E2E i testu interfejsu w przeglądarce."),
            "jakość interfejsu": _rating(None, "Brak porównania screenshotów desktop/mobile oraz danych z konsoli."),
            "bezpieczeństwo": _rating(None, "Wykonano tylko ograniczony statyczny przegląd; nie wykonano pełnego security scan."),
            "poprawność uprawnień": _rating(None, "Snapshot runtime nie potwierdza zakresów tokenów po stronie wszystkich dostawców."),
            "jakość filmów": _rating(None, "Brak pełnego vision/audio review reprezentatywnych renderów."),
            "sensowność strategii inwestycyjnej": _rating(None, "Brak backtestu, out-of-sample i walk-forward."),
            "gotowość infrastruktury AI": _rating(None, "Część workerów jest dostępna, ale brak zweryfikowanego E2E dla głosu, Gmaila i browser handoff."),
            "ogólna gotowość projektu": _rating(None, "Niepełne dane uniemożliwiają uczciwą ocenę 0–100."),
        }
        conflicts = [
            "Status browser Claude/Kimi oznacza tylko przygotowanie supervised handoffu, nie wykonaną analizę zewnętrznego modelu.",
            "Statusy gotowości runtime nie są dowodem poprawności wizualnej UI ani jakości semantycznej filmu.",
            "Paper-only i limity ryzyka są pozytywnym guardrailem, ale nie są dowodem skuteczności strategii.",
        ]
        return _report(
            run_id=run_id,
            key="coordinator",
            title="Atnos — raport jakości, bezpieczeństwa, strategii i gotowości AI",
            summary=(
                f"Koordynator zebrał {len(reports)} raportów; wykrył {critical} krytycznych i {high} wysokich problemów. "
                "Wnioski bez dowodów zostały oznaczone jako niezweryfikowane."
            ),
            works_well=[
                "Raporty są rozdzielone per obszar i mają zapisane dowody oraz ograniczenia.",
                "Handoffy Claude/Kimi nie są mylone z faktycznie odebranymi raportami.",
                "Trading live pozostał zablokowany w ocenie i w kodzie paper-only.",
            ],
            works_wrong=conflicts,
            not_working=[problem["title"] for problem in deduped],
            not_verified=["Wszystkie obszary oznaczone w ratingach jako insufficient evidence."],
            evidence=[
                {"check": "report_comparison", "method": "deduplicate titles and compare audit limitations", "report_keys": [item["key"] for item in reports]},
                {"check": "ratings", "method": "score only when evidence is sufficient", "ratings": ratings},
            ],
            problems=deduped,
            confidence="medium",
            extra={"ratings": ratings, "contradictions_or_limits": conflicts},
        )

    def _delivery_status(self, *, coordinator: dict[str, Any], attempt_gmail_delivery: bool) -> dict[str, Any]:
        try:
            gmail = GmailConnectorService(self.session, self.settings).get_status()
            ready = bool(gmail.connected and gmail.live_write_enabled)
            oauth_status = gmail.oauth_status
        except Exception as exc:
            self.session.rollback()
            return {
                "status": "blocked",
                "reason": f"gmail_status_unavailable: {type(exc).__name__}: {exc}",
                "report_path": coordinator.get("markdown_path"),
                "sent": False,
            }
        if not attempt_gmail_delivery:
            return {
                "status": "not_requested",
                "reason": "Gmail delivery was disabled for this audit run.",
                "report_path": coordinator.get("markdown_path"),
            }
        if not ready:
            return {
                "status": "blocked",
                "reason": "gmail_live_write_unavailable: active Gmail OAuth token with write scope is required.",
                "report_path": coordinator.get("markdown_path"),
                "oauth_status": oauth_status,
                "sent": False,
            }
        return {
            "status": "requires_supervised_delivery",
            "reason": "Gmail is available, but sending reports requires a reviewed draft/action preview; no email was sent by this audit.",
            "report_path": coordinator.get("markdown_path"),
            "sent": False,
        }

    def _write_report(self, run_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
        stem = str(report["key"])
        json_path = run_dir / f"{stem}.json"
        markdown_path = run_dir / f"{stem}.md"
        report["json_path"] = str(json_path)
        report["markdown_path"] = str(markdown_path)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(_render_markdown(report), encoding="utf-8")
        return report


def _report(
    *,
    run_id: str,
    key: str,
    title: str,
    summary: str,
    works_well: list[str],
    works_wrong: list[str],
    not_working: list[str],
    not_verified: list[str],
    evidence: list[dict[str, Any]],
    problems: list[dict[str, Any]],
    confidence: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "key": key,
        "title": title,
        "created_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "works_well": works_well,
        "works_wrong": works_wrong,
        "not_working": not_working,
        "not_verified": not_verified,
        "evidence": evidence,
        "problems": problems,
        "confidence": confidence,
        "actions": {
            "immediate": [item["title"] for item in problems if item["severity"] in {"critical", "high"}],
            "before_deployment": [item["title"] for item in problems if item["severity"] == "medium"],
            "future": [item["title"] for item in problems if item["severity"] == "low"],
        },
        **(extra or {}),
    }


def _problem(
    *,
    severity: Literal["critical", "high", "medium", "low"],
    title: str,
    cause: str,
    fix: str,
    files: list[str],
    verification: str,
    regression_risk: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "title": title,
        "cause": cause,
        "suggested_fix": fix,
        "files": files,
        "verification": verification,
        "regression_risk": regression_risk,
    }


def _rating(value: int | None, reason: str) -> dict[str, Any]:
    return {"score_0_to_100": value, "reason": reason}


def _resolve_ffmpeg(settings: Settings) -> str | None:
    configured = str(settings.ffmpeg_exe or "").strip()
    if configured and (Path(configured).exists() or shutil.which(configured)):
        return configured if Path(configured).exists() else shutil.which(configured)
    detected = shutil.which("ffmpeg")
    if detected:
        return detected
    try:
        import imageio_ffmpeg

        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        return candidate if candidate and Path(candidate).exists() else None
    except Exception:
        return None


def _resolve_headless_browser() -> str | None:
    candidates = (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("chrome") or shutil.which("msedge")


def _run_headless_screenshot(
    *,
    browser: str,
    profile_dir: Path,
    output: Path,
    target: str,
    width: int,
    height: int,
) -> subprocess.CompletedProcess[bytes]:
    """Use Start-Process on Windows because Chromium children can hold Python pipes open."""
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise OSError("PowerShell is required for the Windows headless screenshot adapter.")
    profile_dir.mkdir(parents=True, exist_ok=True)
    arguments = [
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{height}",
        "--virtual-time-budget=4000",
        f"--screenshot={output}",
        target,
    ]
    argument_list = ", ".join(_powershell_literal(item) for item in arguments)
    command = (
        f"$process = Start-Process -FilePath {_powershell_literal(browser)} "
        f"-ArgumentList @({argument_list}) -PassThru -Wait; exit $process.ExitCode"
    )
    return subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
        check=False,
    )


def _powershell_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['title']}", "", f"- Run: `{report['run_id']}`", f"- Pewność: `{report['confidence']}`", "", "## 1. Podsumowanie", str(report["summary"])]
    for heading, key in (
        ("2. Co działa dobrze", "works_well"),
        ("3. Co działa źle", "works_wrong"),
        ("4. Co nie działa", "not_working"),
        ("5. Czego nie udało się sprawdzić", "not_verified"),
    ):
        lines.extend(["", f"## {heading}"])
        values = report.get(key) or []
        lines.extend(f"- {item}" for item in values) if values else lines.append("- Brak.")
    lines.extend(["", "## 6. Poziom ważności problemów"])
    problems = report.get("problems") or []
    if problems:
        for problem in problems:
            lines.append(f"- `{problem['severity']}` {problem['title']}")
    else:
        lines.append("- Brak wykrytych problemów w zakresie wykonanych testów.")
    lines.extend(["", "## 7. Proponowane naprawy"])
    for problem in problems:
        lines.extend(
            [
                f"### {problem['title']}",
                f"- Przyczyna: {problem['cause']}",
                f"- Poprawka: {problem['suggested_fix']}",
                f"- Pliki/moduły: {', '.join(problem['files'])}",
                f"- Weryfikacja: {problem['verification']}",
                f"- Ryzyko regresji: {problem['regression_risk']}",
            ]
        )
    lines.extend(["", "## 8. Lista działań"])
    actions = report["actions"]
    for label, key in (("Do wykonania natychmiast", "immediate"), ("Przed wdrożeniem", "before_deployment"), ("Usprawnienia przyszłościowe", "future")):
        lines.append(f"### {label}")
        values = actions.get(key) or []
        lines.extend(f"- {item}" for item in values) if values else lines.append("- Brak.")
    if report.get("ratings"):
        lines.extend(["", "## Oceny 0–100"])
        for key, rating in report["ratings"].items():
            lines.append(f"- {key}: {rating['score_0_to_100'] if rating['score_0_to_100'] is not None else 'nie oceniono'} — {rating['reason']}")
    lines.extend(["", "## Dowody"])
    lines.append("```json")
    lines.append(json.dumps(report.get("evidence") or [], ensure_ascii=False, indent=2, default=str))
    lines.append("```")
    return "\n".join(lines) + "\n"


def _claude_design_prompt() -> str:
    return """Przygotuj implementowalny redesign desktopowej aplikacji Atnos. Nie projektuj areny modeli. Produkt ma dwie główne osie: Rozmowy oraz Ustawienia/Projekty. Uwzględnij mapę ekranów, nawigację, dashboard, zadania, raporty, agentów, uprawnienia, stany błędu/loading/empty, responsywność i dostępność. Dla każdego ekranu podaj cel, komponenty, zachowanie, błędy i breakpointy. Zaproponuj system designu możliwy w React + TypeScript + Tailwind. Zwróć Markdown z konkretnym planem wdrożenia. Pozostań przy analizie i rekomendacjach, bez wykonywania zmian w kontach ani operacjach finansowych."""


def _kimi_model_prompt() -> str:
    return """Przygotuj rekomendację modeli AI/LLM dla Atnosa: programowanie, code review, testy, UX, research, analiza danych, strategia inwestycyjna paper-only, dokumenty, obrazy, filmy, lokalna prywatność i koszty. Zaproponuj macierz modeli oraz routing według kosztu, jakości, prywatności i krytyczności. Podaj ograniczenia, wymagania sprzętowe i ryzyko vendor lock-in. Pozostań przy analizie i rekomendacjach: bez działań zmieniających konta albo wpływających na środki. Oznacz twierdzenia wymagające aktualnej weryfikacji źródłami."""
