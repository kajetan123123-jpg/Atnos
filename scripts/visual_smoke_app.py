from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright


VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 1024, "height": 768},
    "mobile": {"width": 390, "height": 844},
}
ROUTES = ("chat", "settings")


def _layout_metrics(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const root = document.documentElement;
          const width = window.innerWidth;
          const isInsideHorizontalScroller = (node) => {
            let parent = node.parentElement;
            while (parent && parent !== document.body) {
              const style = getComputedStyle(parent);
              if (['auto', 'scroll'].includes(style.overflowX)) return true;
              parent = parent.parentElement;
            }
            return false;
          };
          const offenders = [...document.querySelectorAll('body *')]
            .filter((node) => {
              const style = getComputedStyle(node);
              if (style.position === 'fixed' && style.visibility === 'hidden') return false;
              if (isInsideHorizontalScroller(node)) return false;
              const rect = node.getBoundingClientRect();
              return rect.width > 1 && rect.right > width + 1 && style.overflowX !== 'auto';
            })
            .slice(0, 12)
            .map((node) => ({
              tag: node.tagName.toLowerCase(),
              text: (node.textContent || '').trim().slice(0, 80),
              right: Math.round(node.getBoundingClientRect().right),
              className: String(node.className || '').slice(0, 120),
            }));
          return {
            viewport_width: width,
            viewport_height: window.innerHeight,
            scroll_width: root.scrollWidth,
            scroll_height: root.scrollHeight,
            horizontal_overflow: root.scrollWidth > width + 1,
            body_text_chars: (document.body.innerText || '').trim().length,
            error_overlay: Boolean(document.querySelector('.vite-error-overlay, #webpack-dev-server-client-overlay, [data-nextjs-dialog]')),
            overflow_offenders: offenders,
          };
        }
        """
    )


def run(base_url: str, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            for profile, viewport in VIEWPORTS.items():
                context = browser.new_context(viewport=viewport, device_scale_factor=1)
                page = context.new_page()
                console_errors: list[str] = []
                page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                page.on("pageerror", lambda error: console_errors.append(str(error)))

                for route in ROUTES:
                    url = f"{base_url.rstrip('/')}/{route}"
                    response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    # Atnos stale monitoruje runtime, więc stan networkidle celowo nie występuje.
                    page.wait_for_timeout(1_500)
                    metrics = _layout_metrics(page)
                    screenshot = output_dir / f"{route}-{profile}.png"
                    page.screenshot(path=str(screenshot), full_page=False)
                    passed = bool(
                        response
                        and response.ok
                        and metrics["body_text_chars"] > 100
                        and not metrics["error_overlay"]
                        and not metrics["horizontal_overflow"]
                        and not metrics["overflow_offenders"]
                        and not console_errors
                    )
                    results.append(
                        {
                            "route": route,
                            "profile": profile,
                            "url": url,
                            "http_status": response.status if response else None,
                            "screenshot": str(screenshot),
                            "console_errors": list(console_errors),
                            "metrics": metrics,
                            "passed": passed,
                        }
                    )
                    console_errors.clear()
                context.close()
        finally:
            browser.close()

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "passed": all(item["passed"] for item in results),
        "results": results,
    }
    report_path = output_dir / "visual-smoke-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprawdza wizualnie główne widoki Atnosa.")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.base_url, args.output_dir)
    print(json.dumps({"passed": report["passed"], "checks": len(report["results"])}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
