from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re

from playwright.sync_api import sync_playwright


VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 1024, "height": 768},
    "mobile": {"width": 390, "height": 844},
}


def _layout_probe(page) -> dict[str, object]:
    return page.evaluate(
        """
        () => {
          const root = document.documentElement;
          const offenders = [...document.querySelectorAll('body *')]
            .filter((element) => {
              const style = getComputedStyle(element);
              if (style.display === 'none' || style.visibility === 'hidden') return false;
              const rect = element.getBoundingClientRect();
              if (rect.width === 0 || rect.height === 0) return false;
              let ancestor = element.parentElement;
              while (ancestor && ancestor !== document.body) {
                const ancestorStyle = getComputedStyle(ancestor);
                if (['auto', 'scroll'].includes(ancestorStyle.overflowX) && ancestor.scrollWidth > ancestor.clientWidth) {
                  return false;
                }
                ancestor = ancestor.parentElement;
              }
              return rect.left < -2 || rect.right > window.innerWidth + 2;
            })
            .slice(0, 20)
            .map((element) => {
              const rect = element.getBoundingClientRect();
              return {
                tag: element.tagName,
                text: (element.textContent || '').trim().slice(0, 100),
                className: String(element.className || '').slice(0, 180),
                left: Math.round(rect.left),
                right: Math.round(rect.right),
                width: Math.round(rect.width),
              };
            });
          return {
            viewport: { width: window.innerWidth, height: window.innerHeight },
            document: { scrollWidth: root.scrollWidth, scrollHeight: root.scrollHeight },
            horizontalOverflow: root.scrollWidth > window.innerWidth + 2,
            offenders,
            errorOverlay: Boolean(document.querySelector('.vite-error-overlay, [data-nextjs-dialog], #webpack-dev-server-client-overlay')),
            bodyTextLength: document.body.innerText.trim().length,
          };
        }
        """
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run visual checks for Atnos Settings and Quality Council.")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--browser-executable",
        default="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "viewports": {},
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(Path(args.browser_executable).resolve()),
            headless=True,
        )
        try:
            for name, viewport in VIEWPORTS.items():
                context = browser.new_context(viewport=viewport)
                page = context.new_page()
                console_errors: list[dict[str, object]] = []
                page_errors: list[str] = []
                failed_requests: list[str] = []
                failed_responses: list[str] = []
                page.on(
                    "console",
                    lambda message: console_errors.append(
                        {"text": message.text, "location": message.location}
                    )
                    if message.type == "error"
                    else None,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on("requestfailed", lambda request: failed_requests.append(f"{request.method} {request.url}: {request.failure}"))
                page.on(
                    "response",
                    lambda response: failed_responses.append(
                        f"{response.status} {response.request.method} {response.url}"
                    )
                    if response.status >= 400
                    else None,
                )

                item: dict[str, object] = {"viewport": viewport}
                try:
                    page.goto(f"{args.base_url.rstrip('/')}/settings", wait_until="domcontentloaded", timeout=30_000)
                    page.get_by_text("Ustawienia", exact=True).first.wait_for(state="visible", timeout=20_000)
                    runtime_button = page.get_by_role("button", name="Runtime", exact=True).first
                    runtime_button.click(timeout=10_000)
                    page.get_by_text("Quality Council", exact=True).wait_for(state="visible", timeout=20_000)
                    # Runtime health probes include local processes and external providers.
                    # Capture the evidence only after the slowest cards have had time to settle.
                    page.wait_for_timeout(12_000)
                    runtime_screenshot = output_dir / f"quality-council-{name}.png"
                    page.screenshot(path=str(runtime_screenshot), full_page=True)
                    item["runtime_screenshot"] = str(runtime_screenshot)
                    item["quality_council_visible"] = True
                    item["layout"] = _layout_probe(page)

                    form_result: dict[str, object] = {
                        "tested": False,
                        "dialogs_present": page.get_by_role("dialog").count(),
                    }
                    add_buttons = page.get_by_role("button", name=re.compile("Dodaj konto", re.I))
                    for index in range(add_buttons.count()):
                        candidate = add_buttons.nth(index)
                        if candidate.is_visible():
                            candidate.click()
                            page.wait_for_timeout(300)
                            account_name = page.locator("#account-name")
                            account_name.wait_for(state="visible", timeout=5_000)
                            box = account_name.bounding_box()
                            form_result = {
                                "tested": True,
                                "visible": True,
                                "box": box,
                                "inside_viewport": bool(
                                    box
                                    and box["x"] >= -2
                                    and box["y"] >= -2
                                    and box["x"] + box["width"] <= viewport["width"] + 2
                                    and box["y"] + box["height"] <= viewport["height"] + 2
                                ),
                                "form_controls": page.locator("aside input, aside textarea, aside select, aside button").count(),
                                "dialogs_present": page.get_by_role("dialog").count(),
                            }
                            form_screenshot = output_dir / f"account-form-{name}.png"
                            page.screenshot(path=str(form_screenshot), full_page=False)
                            form_result["screenshot"] = str(form_screenshot)
                            page.get_by_role("button", name="Anuluj", exact=True).click()
                            break
                    if not form_result["tested"]:
                        form_result["reason"] = "Sidebar account form is not part of the mobile layout."
                        form_result["page_form_controls"] = page.locator("input, textarea, select, button").count()
                    item["account_form"] = form_result
                except Exception as exc:
                    item["error"] = f"{type(exc).__name__}: {exc}"
                    error_screenshot = output_dir / f"error-{name}.png"
                    page.screenshot(path=str(error_screenshot), full_page=True)
                    item["error_screenshot"] = str(error_screenshot)
                item["console_errors"] = console_errors
                item["page_errors"] = page_errors
                item["failed_requests"] = failed_requests
                item["failed_responses"] = failed_responses
                report["viewports"][name] = item
                context.close()
        finally:
            browser.close()

    failures: list[str] = []
    for name, raw in report["viewports"].items():
        item = raw
        if item.get("error"):
            failures.append(f"{name}: {item['error']}")
            continue
        layout = item.get("layout") or {}
        if layout.get("horizontalOverflow"):
            failures.append(f"{name}: horizontal overflow")
        if layout.get("offenders"):
            failures.append(f"{name}: visible elements exceed viewport")
        if layout.get("errorOverlay"):
            failures.append(f"{name}: framework error overlay")
        if item.get("page_errors"):
            failures.append(f"{name}: page errors")
        if item.get("failed_requests"):
            failures.append(f"{name}: failed requests")
        if item.get("failed_responses"):
            failures.append(f"{name}: HTTP error responses")
        account_form = item.get("account_form") or {}
        if account_form.get("tested") and not account_form.get("inside_viewport"):
            failures.append(f"{name}: account form exceeds viewport")
    report["failures"] = failures
    report["passed"] = not failures
    report_path = output_dir / "visual-check-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "passed": not failures, "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
