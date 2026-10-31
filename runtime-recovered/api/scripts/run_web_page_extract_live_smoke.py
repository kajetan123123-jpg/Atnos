"""Live smoke + Wealth Blueprint WEB_PAGE_EXTRACT fact-check pilot.

Read-only public pages only. Browser/stealth stay OFF.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atnos.integrations.capability_runtime import execute_capability
from atnos.integrations.safe_web_fetch import clear_cache
from atnos.integrations.web_page_extract import fact_check_claim_against_url

REPORT_DIR = Path(r"E:\Atnos\App\reports")
DOCS_DIR = Path(r"E:\Atnos\App\docs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def smoke_case(name: str, url: str, **params) -> dict:
    started = _now()
    result = execute_capability("WEB_PAGE_EXTRACT", parameters={"url": url, **params}, project_id="wealth_blueprint")
    output = result.get("output") if isinstance(result.get("output"), dict) else {}
    quality = output.get("extraction_quality") if isinstance(output.get("extraction_quality"), dict) else {}
    return {
        "name": name,
        "requested_url": url,
        "status": result.get("status"),
        "http_status": (result.get("diagnostics") or {}).get("http_status") or output.get("http_status"),
        "final_url": output.get("final_url"),
        "title": output.get("title"),
        "block_count": quality.get("block_count"),
        "extracted_chars": quality.get("extracted_chars"),
        "main_content_confidence": quality.get("main_content_confidence"),
        "cache": (result.get("diagnostics") or {}).get("cache") or output.get("cache"),
        "content_hash": (result.get("provenance") or {}).get("content_hash") or output.get("content_hash"),
        "browser": "OFF",
        "stealth": "OFF",
        "errors": result.get("errors") or [],
        "started_at": started,
        "finished_at": _now(),
    }


def main() -> int:
    clear_cache()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    cases = [
        smoke_case("A_simple_html", "https://example.com/"),
        smoke_case("B_docs_page", "https://www.w3.org/TR/PNG/"),
        smoke_case(
            "C_boilerplate_heavy",
            "https://www.consumerfinance.gov/consumer-tools/educator-tools/youth-financial-education/",
        ),
        smoke_case("D_controlled_404", "https://example.com/atnos-missing-page-does-not-exist-404"),
    ]

    # Wealth Blueprint fact-check: emergency fund claim against CFPB page.
    claim = "An emergency fund is money set aside to cover unexpected expenses"
    source = "https://www.consumerfinance.gov/an-essential-guide-to-building-an-emergency-fund/"
    fact = fact_check_claim_against_url(claim, source)
    runtime = execute_capability(
        "WEB_PAGE_EXTRACT",
        parameters={"url": source},
        project_id="wealth_blueprint",
    )

    report = {
        "generated_at": _now(),
        "capability": "WEB_PAGE_EXTRACT",
        "provider_impl": "scrapling (not agent-facing)",
        "browser": "OFF",
        "stealth": "OFF",
        "live_smoke": cases,
        "wealth_blueprint_fact_check": {
            "claim": claim,
            "source_url": source,
            "verdict": fact.get("verdict"),
            "claim_status": fact.get("claim_status"),
            "block_ids": fact.get("block_ids"),
            "blocks": fact.get("blocks"),
            "content_hash": fact.get("content_hash"),
            "brain_class": fact.get("brain_class"),
            "provenance": fact.get("provenance"),
            "source_quality": fact.get("source_quality"),
            "extraction_quality": fact.get("extraction_quality"),
            "runtime_status": runtime.get("status"),
            "runtime_block_count": ((runtime.get("output") or {}).get("extraction_quality") or {}).get("block_count"),
            "invented_value": False,
        },
    }

    out_json = REPORT_DIR / "atnos_web_page_extract_live_smoke.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = []
    md.append("# WEB_PAGE_EXTRACT live smoke + WB fact-check")
    md.append("")
    md.append(f"Generated: `{report['generated_at']}`")
    md.append("")
    md.append("Browser: **OFF** · Stealth: **OFF** · Fetch: Atnos safe_web_fetch · Parser: Scrapling")
    md.append("")
    md.append("## Live smoke")
    md.append("")
    md.append("| Case | Status | HTTP | Title | Blocks | Chars | Cache |")
    md.append("|---|---|---|---|---|---|---|")
    for case in cases:
        md.append(
            f"| {case['name']} | {case['status']} | {case.get('http_status')} | "
            f"{(case.get('title') or '')[:40]} | {case.get('block_count')} | "
            f"{case.get('extracted_chars')} | {case.get('cache')} |"
        )
    md.append("")
    md.append("## Wealth Blueprint fact-check E2E")
    md.append("")
    md.append(f"- Claim: {claim}")
    md.append(f"- URL: {source}")
    md.append(f"- Verdict: **{fact.get('verdict')}**")
    md.append(f"- Claim status: {fact.get('claim_status')}")
    md.append(f"- Block IDs: {fact.get('block_ids')}")
    md.append(f"- Brain class: {fact.get('brain_class')}")
    md.append(f"- Content hash: `{fact.get('content_hash')}`")
    md.append(f"- Runtime WEB_PAGE_EXTRACT: {runtime.get('status')}")
    md.append("")
    if fact.get("blocks"):
        md.append("### Evidence snippets")
        for block in fact["blocks"][:3]:
            md.append(f"- `{block.get('block_id')}` ({block.get('score')}): {block.get('text')}")
    md.append("")
    md_path = REPORT_DIR / "atnos_web_page_extract_live_smoke.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"json": str(out_json), "md": str(md_path), "verdict": fact.get("verdict"), "smoke": [c["status"] for c in cases]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
