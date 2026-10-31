from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DATE = datetime.now().strftime("%Y%m%d")
REPORT_PATH = ROOT / f"desktop_browser_audit_{DATE}.md"
ARTIFACT_DIR = ROOT / f"desktop_browser_audit_{DATE}_artifacts"
SCREENSHOT_PATH = ARTIFACT_DIR / "atnos_test_screenshot.png"

SCAN_ROOTS = [
    ROOT / "runtime-recovered" / "api" / "src",
    ROOT / "runtime-recovered" / "api" / "tests",
    ROOT / "scripts",
    ROOT / "docs",
]
EXCLUDED_PARTS = {
    ".venv",
    "node_modules",
    "dist",
    "build",
    "data",
    "tool-caches",
    "__pycache__",
    "site-packages",
}
TOOL_TERMS = [
    "playwright",
    "selenium",
    "pyautogui",
    "pygetwindow",
    "subprocess",
    "webbrowser",
    "xdotool",
    "wnck",
    "ewmh",
    "pynput",
    "keyboard",
    "mouse",
    "screen",
    "screenshot",
    "clip",
    "clipboard",
    "desktop",
    "browser",
    "chromium",
    "firefox",
]


@dataclass
class CommandResult:
    name: str
    status: str
    output: str
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class CapabilityResult:
    capability: str
    status: str
    atnos_uses_it: bool
    notes: str
    log_name: str


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    fixes: list[str] = []
    inventory = scan_inventory()
    ensure_dependencies(fixes)

    checks = run_dependency_checks()
    live_tests = run_live_tests()
    matrix = build_capability_matrix(inventory, checks, live_tests)
    opportunities = build_untapped_opportunities(matrix)

    write_json("inventory.json", inventory)
    write_json("dependency_checks.json", [asdict(item) for item in checks])
    write_json("live_tests.json", [asdict(item) for item in live_tests])
    write_json("capability_matrix.json", [asdict(item) for item in matrix])
    write_json("fixes.json", fixes)
    REPORT_PATH.write_text(render_report(inventory, checks, live_tests, matrix, fixes, opportunities), encoding="utf-8")

    print(f"REPORT={REPORT_PATH}")
    print(f"ARTIFACTS={ARTIFACT_DIR}")
    return 0


def scan_inventory() -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    terms_by_file: dict[str, list[str]] = {}
    term_counts = {term: 0 for term in TOOL_TERMS}
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lowered = text.lower()
            found_terms = sorted({term for term in TOOL_TERMS if term in lowered})
            if not found_terms:
                continue
            rel = str(path.relative_to(ROOT))
            lines = []
            for index, line in enumerate(text.splitlines(), start=1):
                low_line = line.lower()
                if any(term in low_line for term in found_terms):
                    lines.append({"line": index, "text": line.strip()[:240]})
            for term in found_terms:
                term_counts[term] += 1
            terms_by_file[rel] = found_terms
            hits.append(
                {
                    "file": rel,
                    "terms": found_terms,
                    "line_hits": lines[:100],
                    "hit_count": len(lines),
                    "probable_purpose": infer_purpose(rel, found_terms),
                }
            )
    return {
        "scan_roots": [str(root) for root in SCAN_ROOTS],
        "hit_files": hits,
        "terms_by_file": terms_by_file,
        "term_counts": term_counts,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version,
            "executable": sys.executable,
        },
    }


def infer_purpose(path: str, terms: list[str]) -> str:
    lowered = path.lower()
    if "browser" in lowered:
        return "browser automation / page observation"
    if "desktop" in lowered:
        return "desktop app control / local UI operation"
    if "visual" in lowered or "screenshot" in terms:
        return "visual QA / screenshot capture"
    if "test" in lowered:
        return "test coverage"
    if "oauth" in lowered:
        return "OAuth browser callback/setup"
    if "subprocess" in terms:
        return "process launch / shell integration"
    return "general automation support"


def ensure_dependencies(fixes: list[str]) -> None:
    packages = {
        "playwright": "playwright>=1.54.0,<2.0.0",
        "pyautogui": "pyautogui>=0.9.54,<1.0.0",
        "pynput": "pynput>=1.8.0,<2.0.0",
        "pyperclip": "pyperclip>=1.9.0,<2.0.0",
        "PIL": "Pillow>=11.0.0,<12.0.0",
    }
    for module, package in packages.items():
        if can_import(module):
            continue
        result = run_command(
            f"install_{module}",
            [sys.executable, "-m", "pip", "install", package, "-q"],
            timeout=180,
        )
        fixes.append(f"{module}: {result.status} {result.output.strip()[:300]}")
    launch_check = run_python_snippet(
        "playwright_chromium_probe_for_install",
        """
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    b.close()
print("chromium_ok")
""",
        timeout=60,
    )
    if launch_check.status != "PASS":
        install = run_command(
            "playwright_install_chromium",
            [sys.executable, "-m", "playwright", "install", "chromium"],
            timeout=600,
        )
        fixes.append(f"playwright chromium install: {install.status} {install.output.strip()[:500]}")


def can_import(module: str) -> bool:
    code = f"import {module}; print('ok')"
    return run_python_snippet(f"import_{module}", code, timeout=20).status == "PASS"


def run_dependency_checks() -> list[CommandResult]:
    checks = [
        (
            "playwright_import",
            "import playwright; print('playwright OK')",
        ),
        (
            "playwright_browser_launch",
            """
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch(headless=True)
b.close()
p.stop()
print('playwright browser launch OK')
""",
        ),
        ("pyautogui_import", "import pyautogui; print('pyautogui OK')"),
        (
            "xdotool_check",
            """
import shutil, platform
tool = shutil.which('xdotool')
if tool:
    print('xdotool OK')
elif platform.system().lower() == 'windows':
    print('xdotool N/A on Windows')
else:
    print('xdotool MISSING')
""",
        ),
        ("pynput_import", "import pynput; print('pynput OK')"),
        ("pil_imagegrab_import", "from PIL import ImageGrab; print('screenshot OK')"),
        ("pyperclip_import", "import pyperclip; print('clipboard OK')"),
    ]
    return [run_python_snippet(name, code, timeout=60) for name, code in checks]


def run_live_tests() -> list[CommandResult]:
    tests: list[tuple[str, Callable[[], str]]] = [
        ("open_headless_url", test_open_url),
        ("webpage_screenshot", test_webpage_screenshot),
        ("form_interaction", test_form_interaction),
        ("structured_data_extraction", test_structured_data_extraction),
        ("network_interception", test_network_interception),
        ("browser_download", test_browser_download),
        ("native_desktop_app_launch", test_native_app_launch),
        ("clipboard_read_write", test_clipboard),
        ("display_detection", test_display_detection),
        ("browser_js_execution", test_js_execution),
    ]
    results: list[CommandResult] = []
    for name, func in tests:
        started = time.monotonic()
        try:
            output = func()
            status = "PASS" if output.startswith("PASS") else "WARN" if output.startswith("WARN") else "INFO"
            results.append(CommandResult(name=name, status=status, output=output, duration_s=round(time.monotonic() - started, 3)))
        except Exception as exc:
            results.append(
                CommandResult(
                    name=name,
                    status="FAIL",
                    output="",
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                    duration_s=round(time.monotonic() - started, 3),
                )
            )
    return results


def test_open_url() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.example.com", timeout=15000)
        title = page.title()
        browser.close()
    assert "Example" in title, f"Unexpected title: {title}"
    return f"PASS: opened browser, got title: {title}"


def test_webpage_screenshot() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.example.com", timeout=15000)
        page.screenshot(path=str(SCREENSHOT_PATH))
        browser.close()
    size = SCREENSHOT_PATH.stat().st_size
    assert size > 5000, f"Screenshot suspiciously small: {size} bytes"
    return f"PASS: screenshot saved, size={size} bytes, path={SCREENSHOT_PATH}"


def test_form_interaction() -> str:
    from playwright.sync_api import sync_playwright

    fallback_used = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://httpbin.org/forms/post", timeout=20000)
        try:
            page.wait_for_selector('input[name="custname"]', timeout=5000)
        except Exception:
            fallback_used = True
            page.set_content(
                """
                <form>
                  <input name="custname" />
                  <input name="custtel" />
                  <select name="size">
                    <option value="small">small</option>
                    <option value="medium">medium</option>
                    <option value="large">large</option>
                  </select>
                </form>
                """
            )
        page.fill('input[name="custname"]', "AtnOS_Test")
        page.fill('input[name="custtel"]', "123456789")
        page.select_option('select[name="size"]', "medium")
        val = page.input_value('input[name="custname"]')
        browser.close()
    assert val == "AtnOS_Test", f"Field value mismatch: {val}"
    suffix = " (fallback local fixture used after httpbin selector timeout)" if fallback_used else ""
    return f"PASS: form interaction works{suffix}"


def test_structured_data_extraction() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://news.ycombinator.com", timeout=20000)
        titles = page.eval_on_selector_all(".titleline > a", "els => els.slice(0,5).map(e => e.innerText)")
        browser.close()
    assert len(titles) == 5, f"Expected 5 titles, got {len(titles)}"
    return f"PASS: extracted {len(titles)} HN titles: {titles[:2]}"


def test_network_interception() -> str:
    from playwright.sync_api import sync_playwright

    requests_log: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("request", lambda request: requests_log.append(request.url))
        page.goto("https://httpbin.org/get", timeout=20000)
        browser.close()
    assert requests_log, "No requests captured"
    return f"PASS: network interception works, captured {len(requests_log)} requests"


def test_browser_download() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        page.set_content(
            """
            <a id="download" download="atnos-browser-download.txt"
               href="data:text/plain;charset=utf-8,atnos_download_test">download</a>
            """
        )
        with page.expect_download(timeout=15000) as download_info:
            page.click("#download")
        download = download_info.value
        path = Path(download.path())
        size = path.stat().st_size
        browser.close()
    assert size > 0
    return f"PASS: file download works, size={size}, path={path}"


def test_native_app_launch() -> str:
    if platform.system().lower() == "windows":
        executable = shutil.which("notepad.exe")
        if not executable:
            return "WARN: notepad.exe not found"
        proc = subprocess.Popen([executable])
        time.sleep(1.0)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return "PASS: native desktop app launch possible (notepad.exe)"

    editors = ["gedit", "mousepad", "xed", "nano", "kate"]
    for editor in editors:
        if shutil.which(editor):
            proc = subprocess.Popen([editor, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.wait(timeout=5)
            return f"PASS: desktop app launch possible ({editor})"
    return "WARN: no standard GUI editor found - headless environment likely"


def test_clipboard() -> str:
    import pyperclip

    pyperclip.copy("atnos_clipboard_test_12345")
    val = pyperclip.paste()
    assert val == "atnos_clipboard_test_12345", f"Clipboard mismatch: {val}"
    return "PASS: clipboard read/write works"


def test_display_detection() -> str:
    if platform.system().lower() == "windows":
        user32 = ctypes.windll.user32
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        assert width > 0 and height > 0, f"Invalid display metrics: {width}x{height}"
        return f"PASS: display detected via Windows user32: {width}x{height}"
    for command in (["xrandr", "--query"], ["xdpyinfo"]):
        if not shutil.which(command[0]):
            continue
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return f"PASS: display detected via {command[0]}\n{result.stdout[:300]}"
    return "INFO: no display detected - headless environment, browser-only mode applicable"


def test_js_execution() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.example.com", timeout=15000)
        result = page.evaluate("() => ({ ua: navigator.userAgent, w: window.innerWidth, h: window.innerHeight })")
        browser.close()
    assert result["ua"], "No user agent returned"
    return f"PASS: JS execution works - UA={result['ua'][:40]}, {result['w']}x{result['h']}"


def build_capability_matrix(
    inventory: dict[str, Any],
    checks: list[CommandResult],
    live_tests: list[CommandResult],
) -> list[CapabilityResult]:
    term_counts = inventory["term_counts"]
    by_name = {item.name: item for item in [*checks, *live_tests]}
    definitions = [
        ("Headless browser navigation", "open_headless_url", term_counts.get("playwright", 0) > 0 or term_counts.get("browser", 0) > 0),
        ("Screenshot capture", "webpage_screenshot", term_counts.get("screenshot", 0) > 0),
        ("Form interaction", "form_interaction", term_counts.get("playwright", 0) > 0),
        ("DOM data extraction", "structured_data_extraction", term_counts.get("playwright", 0) > 0 or term_counts.get("browser", 0) > 0),
        ("Network interception", "network_interception", term_counts.get("playwright", 0) > 0),
        ("File download via browser", "browser_download", term_counts.get("browser", 0) > 0 or term_counts.get("download", 0) > 0),
        ("Native app launch", "native_desktop_app_launch", term_counts.get("subprocess", 0) > 0 or term_counts.get("desktop", 0) > 0),
        ("Clipboard I/O", "clipboard_read_write", term_counts.get("clipboard", 0) > 0 or term_counts.get("clip", 0) > 0),
        ("Display detection", "display_detection", term_counts.get("screen", 0) > 0 or term_counts.get("desktop", 0) > 0),
        ("JS execution in browser", "browser_js_execution", term_counts.get("playwright", 0) > 0),
    ]
    matrix: list[CapabilityResult] = []
    for capability, test_name, used in definitions:
        result = by_name.get(test_name)
        status = result.status if result is not None else "UNKNOWN"
        notes = (result.output or result.error or "").strip().replace("\n", " ")[:500] if result is not None else "No test result"
        matrix.append(
            CapabilityResult(
                capability=capability,
                status=status,
                atnos_uses_it=used,
                notes=notes,
                log_name=test_name,
            )
        )
    return matrix


def build_untapped_opportunities(matrix: list[CapabilityResult]) -> list[str]:
    suggestions = {
        "Headless browser navigation": "Dodać collector stron referencyjnych bez ręcznego klikania i bez zależności od UI.",
        "Screenshot capture": "Używać screenshotów do QA miniatur, landing pages i YouTube Studio przed publikacją.",
        "Form interaction": "Zautomatyzować bezpieczne wypełnianie formularzy konfiguracji kont i draftów, z blokadą przed submit bez zgody.",
        "DOM data extraction": "Zasilić intelligence realnymi DOM signals z publicznych stron, gdy API nie wystarcza.",
        "Network interception": "Wykrywać nieudokumentowane endpointy dashboardów i monitorować status uploadów bez scrapowania tekstu.",
        "File download via browser": "Pobierać exporty raportów/assetów z narzędzi SaaS do lokalnego pipeline.",
        "Native app launch": "Sterować lokalnymi narzędziami typu Obsidian, ComfyUI, edytory i viewer QA.",
        "Clipboard I/O": "Wspierać workflow człowiek-agent: przygotuj tekst/link i włóż do schowka do ręcznego potwierdzenia.",
        "Display detection": "Dobierać tryb headed/headless i rozdzielczość screenshotów zależnie od aktywnego ekranu.",
        "JS execution in browser": "Robić lekkie audyty stron i ekstrakcję metryk bez budowania osobnego scrapera.",
    }
    return [
        f"{item.capability}: {suggestions[item.capability]}"
        for item in matrix
        if item.status == "PASS" and not item.atnos_uses_it
    ]


def render_report(
    inventory: dict[str, Any],
    checks: list[CommandResult],
    live_tests: list[CommandResult],
    matrix: list[CapabilityResult],
    fixes: list[str],
    opportunities: list[str],
) -> str:
    lines = [
        "# Atnos - Desktop & Browser Capability Audit",
        f"Data: {datetime.now(UTC).isoformat()}",
        f"System: `{platform.platform()}`",
        f"Python: `{sys.executable}`",
        "",
        "## Inventory",
        f"- Files with automation/tooling hits: {len(inventory['hit_files'])}",
        f"- Platform: {inventory['platform']['system']} {inventory['platform']['release']}",
        "",
        "### Tool Term Counts",
    ]
    lines.extend(f"- `{term}`: {count}" for term, count in inventory["term_counts"].items() if count)
    lines.extend(
        [
            "",
            "## Capability Matrix",
            "| Capability | Status | Atnos Currently Uses It | Notes |",
            "|---|---:|---:|---|",
        ]
    )
    for item in matrix:
        lines.append(
            f"| {item.capability} | {item.status} | {'Y' if item.atnos_uses_it else 'N'} | {item.notes.replace('|', '/')} |"
        )
    lines.extend(["", "## Dependency Checks"])
    lines.extend(_render_logs(checks))
    lines.extend(["", "## Live Test Logs"])
    lines.extend(_render_logs(live_tests))
    lines.extend(["", "## Fixes Implemented"])
    if fixes:
        lines.extend(f"- {item}" for item in fixes)
    else:
        lines.append("- No dependency fixes were needed.")
    lines.extend(["", "## Untapped Opportunities"])
    if opportunities:
        lines.extend(f"- **UNTAPPED OPPORTUNITY** {item}" for item in opportunities)
    else:
        lines.append("- No passing unused capability detected by static inventory.")
    lines.extend(
        [
            "",
            "## Environment Constraints",
            f"- OS: `{platform.system()}`; Linux-only tools like `xdotool` and `xrandr` are not expected on Windows.",
            "- Headless Chromium works if the Playwright tests passed; native desktop control is separate from browser automation.",
            f"- Screenshot artifact: `{SCREENSHOT_PATH}`",
            "",
            "## Artifacts",
            f"- `{ARTIFACT_DIR / 'inventory.json'}`",
            f"- `{ARTIFACT_DIR / 'dependency_checks.json'}`",
            f"- `{ARTIFACT_DIR / 'live_tests.json'}`",
            f"- `{ARTIFACT_DIR / 'capability_matrix.json'}`",
            f"- `{ARTIFACT_DIR / 'fixes.json'}`",
            "",
        ]
    )
    return "\n".join(lines)


def _render_logs(results: list[CommandResult]) -> list[str]:
    lines: list[str] = []
    for item in results:
        lines.extend(
            [
                f"### {item.name}: {item.status}",
                f"- duration_s: `{item.duration_s}`",
                "",
                "```text",
                (item.output or "").strip() or "(no stdout)",
            ]
        )
        if item.error:
            lines.extend(["", "ERROR:", item.error.strip()])
        lines.extend(["```", ""])
    return lines


def run_python_snippet(name: str, code: str, *, timeout: int) -> CommandResult:
    return run_command(name, [sys.executable, "-c", textwrap.dedent(code).strip()], timeout=timeout)


def run_command(name: str, command: list[str], *, timeout: int) -> CommandResult:
    started = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = "\n".join(part for part in [proc.stdout, proc.stderr] if part)
        status = "PASS" if proc.returncode == 0 else "FAIL"
        return CommandResult(name=name, status=status, output=output, duration_s=round(time.monotonic() - started, 3))
    except Exception as exc:
        return CommandResult(
            name=name,
            status="FAIL",
            output="",
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            duration_s=round(time.monotonic() - started, 3),
        )


def write_json(name: str, value: Any) -> None:
    (ARTIFACT_DIR / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
