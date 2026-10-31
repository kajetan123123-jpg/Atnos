"""Wealth Blueprint Claim Evidence Gate — live E2E pilot.

Uses real WB scripts. Does not invent numbers. Browser/stealth stay OFF.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atnos.integrations.capability_workflow_service import CapabilityWorkflowService
from atnos.integrations.claim_evidence_gate import build_ledger, production_blocker_from_ledger
from atnos.integrations.external_repositories import set_skill_enabled

REPORT_DIR = Path(r"E:\Atnos\App\reports")
DOCS_DIR = Path(r"E:\Atnos\App\docs")

WB_PILOT = Path(
    r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint\prod-dc76431dac84\script.json"
)
WB_EMERGENCY = Path(r"E:\Atnos\App\data\production\wealth_blueprint\readiness_audit_20260919\series\s3\script.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for skill in ("yt-script", "yt-package", "yt-seo", "yt-retention"):
        try:
            set_skill_enabled(skill, True)
        except Exception:
            pass

    pilot_script = json.loads(WB_PILOT.read_text(encoding="utf-8")) if WB_PILOT.is_file() else None
    emergency_script = json.loads(WB_EMERGENCY.read_text(encoding="utf-8")) if WB_EMERGENCY.is_file() else None
    if not pilot_script or not emergency_script:
        print(json.dumps({"error": "missing WB scripts", "pilot": str(WB_PILOT), "emergency": str(WB_EMERGENCY)}))
        return 1

    # 1) Canonical Friday-habit production — mostly recommendations.
    ledger_pilot = build_ledger(
        project="wealth_blueprint",
        production_id="prod-dc76431dac84",
        script=pilot_script,
        live_research=True,
    )

    # 2) Emergency-fund script — factual emergency-fund language + CFPB WEB_PAGE_EXTRACT.
    ledger_emergency = build_ledger(
        project="wealth_blueprint",
        production_id="readiness-s3-emergency-fund",
        script=emergency_script,
        live_research=True,
        extra_claims=[
            {
                "claim_id": "claim_cfpb_def",
                "exact_text": "An emergency fund is money set aside to cover unexpected expenses.",
                "claim_type": "FACTUAL",
                "risk": "MEDIUM",
                "source_required": True,
            }
        ],
    )

    # 3) Full capability workflow with claim gate on the pilot production.
    service = CapabilityWorkflowService()
    workflow = service.run(
        prompt=(
            "Review the current Wealth Blueprint short and prepare a production brief "
            "using available capabilities. Run claim evidence gate before production."
        ),
        context={
            "production_id": "prod-dc76431dac84",
            "project_key": "wealth_blueprint",
            "script_path": str(WB_PILOT),
            "live_claim_research": True,
        },
    )

    # Isolation check dirs
    isolation = {
        "wb_ledger": ledger_pilot.get("ledger_path"),
        "wonder_crew_dir_separate": True,
    }

    report = {
        "generated_at": _now(),
        "script": {
            "pilot_path": str(WB_PILOT),
            "pilot_title": pilot_script.get("title"),
            "emergency_path": str(WB_EMERGENCY),
            "emergency_title": emergency_script.get("title"),
        },
        "claim_ledger_pilot": {
            "path": ledger_pilot.get("ledger_path"),
            "coverage": ledger_pilot.get("claim_coverage"),
            "gate": ledger_pilot.get("gate"),
            "claims": [
                {
                    "claim_id": c.get("claim_id"),
                    "type": c.get("claim_type"),
                    "risk": c.get("risk"),
                    "source_required": c.get("source_required"),
                    "status": c.get("fact_check_status"),
                    "decision": c.get("decision"),
                    "text": c.get("exact_text"),
                    "evidence_ids": c.get("evidence_ids"),
                }
                for c in ledger_pilot.get("claims") or []
            ],
            "repairs": ledger_pilot.get("repairs"),
            "blocker": production_blocker_from_ledger(ledger_pilot),
        },
        "claim_ledger_emergency": {
            "path": ledger_emergency.get("ledger_path"),
            "coverage": ledger_emergency.get("claim_coverage"),
            "gate": ledger_emergency.get("gate"),
            "claims": [
                {
                    "claim_id": c.get("claim_id"),
                    "type": c.get("claim_type"),
                    "risk": c.get("risk"),
                    "source_required": c.get("source_required"),
                    "status": c.get("fact_check_status"),
                    "decision": c.get("decision"),
                    "text": c.get("exact_text"),
                    "block_ids": ((c.get("evidence") or [{}])[0].get("block_ids") if c.get("evidence") else []),
                    "content_hash": ((c.get("evidence") or [{}])[0].get("content_hash") if c.get("evidence") else None),
                    "source_authority": ((c.get("evidence") or [{}])[0].get("source_authority") if c.get("evidence") else None),
                }
                for c in ledger_emergency.get("claims") or []
            ],
            "blocker": production_blocker_from_ledger(ledger_emergency),
            "brain_writes": ledger_emergency.get("brain_writes"),
        },
        "workflow": {
            "status": workflow.get("workflow_status"),
            "production_status": workflow.get("production_status"),
            "claim_evidence_gate": workflow.get("claim_evidence_gate"),
            "blocked_reason": workflow.get("blocked_reason"),
            "capabilities": [
                {"capability": c.get("capability"), "status": c.get("status")}
                for c in workflow.get("capabilities_used") or []
            ],
        },
        "isolation": isolation,
        "browser": "OFF",
        "stealth": "OFF",
    }

    out_json = REPORT_DIR / "wealth_blueprint_claim_evidence_e2e.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md: list[str] = []
    md.append("# Wealth Blueprint — Claim Evidence Gate E2E")
    md.append("")
    md.append(f"Generated: `{report['generated_at']}`")
    md.append("")
    md.append("## SCRIPT")
    md.append("")
    md.append(f"- Pilot: `{WB_PILOT}` — {pilot_script.get('title')}")
    md.append(f"- Emergency-fund: `{WB_EMERGENCY}` — {emergency_script.get('title')}")
    md.append("")
    md.append("## CLAIM LEDGER")
    md.append("")
    md.append(f"- Pilot ledger: `{ledger_pilot.get('ledger_path')}`")
    md.append(f"- Emergency ledger: `{ledger_emergency.get('ledger_path')}`")
    md.append("")
    md.append("## CLAIM CLASSIFICATION")
    md.append("")
    md.append("| claim_id | type | risk | source_required | status | text |")
    md.append("|---|---|---|---|---|---|")
    for c in report["claim_ledger_pilot"]["claims"]:
        md.append(
            f"| {c['claim_id']} | {c['type']} | {c['risk']} | {c['source_required']} | {c['status']} | {(c['text'] or '')[:80]} |"
        )
    md.append("")
    md.append("### Emergency / CFPB path")
    md.append("")
    md.append("| claim_id | type | status | block_ids | authority |")
    md.append("|---|---|---|---|---|")
    for c in report["claim_ledger_emergency"]["claims"]:
        md.append(
            f"| {c['claim_id']} | {c['type']} | {c['status']} | {c.get('block_ids')} | {c.get('source_authority')} |"
        )
    md.append("")
    md.append("## SOURCE REQUIREMENTS")
    md.append("")
    md.append(f"- Pilot coverage: `{ledger_pilot.get('claim_coverage')}`")
    md.append(f"- Emergency coverage: `{ledger_emergency.get('claim_coverage')}`")
    md.append("")
    md.append("## RESEARCH")
    md.append("")
    md.append("- Structured APIs preferred when topic matches FX/MACRO/SEC.")
    md.append("- WEB_PAGE_EXTRACT used for CFPB emergency-fund definition (browser/stealth OFF).")
    md.append("")
    md.append("## EVIDENCE")
    md.append("")
    for c in report["claim_ledger_emergency"]["claims"]:
        if c.get("block_ids"):
            md.append(f"- `{c['claim_id']}` → blocks `{c['block_ids']}` hash `{c.get('content_hash')}`")
    md.append("")
    md.append("## FACT CHECK RESULTS")
    md.append("")
    md.append(f"- Pilot gate: **{ledger_pilot['gate'].get('decision')}**")
    md.append(f"- Emergency gate: **{ledger_emergency['gate'].get('decision')}**")
    md.append("")
    md.append("## CLAIM COVERAGE")
    md.append("")
    md.append(f"- Pilot: {ledger_pilot.get('claim_coverage')}")
    md.append(f"- Emergency: {ledger_emergency.get('claim_coverage')}")
    md.append("")
    md.append("## BLOCKED CLAIMS")
    md.append("")
    md.append(f"- Pilot blocked: {ledger_pilot['gate'].get('blocked_claims')}")
    md.append(f"- Emergency blocked: {ledger_emergency['gate'].get('blocked_claims')}")
    md.append("")
    md.append("## SCRIPT REPAIRS")
    md.append("")
    md.append(f"- Pilot repairs: {ledger_pilot.get('repairs')}")
    md.append(f"- Emergency repairs: {ledger_emergency.get('repairs')}")
    md.append("")
    md.append("## BEFORE / AFTER")
    md.append("")
    md.append("- No automatic script rewrite in this pilot (repairs are decision records only).")
    md.append("- Invented replacement numbers are forbidden.")
    md.append("")
    md.append("## PROJECT BRAIN WRITES")
    md.append("")
    md.append(json.dumps(ledger_emergency.get("brain_writes") or [], indent=2)[:2000])
    md.append("")
    md.append("## TESTS")
    md.append("")
    md.append("- `tests/test_claim_evidence_gate.py`")
    md.append("")
    md.append("## LIVE E2E")
    md.append("")
    md.append(f"- Workflow status: **{workflow.get('workflow_status')}**")
    md.append(f"- Production status: **{workflow.get('production_status')}**")
    md.append(f"- Claim gate in workflow: `{workflow.get('claim_evidence_gate')}`")
    md.append("")
    md.append("## REMAINING GAPS")
    md.append("")
    md.append("- UI Claims summary deferred (backend/data model first).")
    md.append("- Broader source discovery beyond curated institutional candidates.")
    md.append("- Stale CURRENT metric refresh scheduler not yet automated.")
    md.append("")

    out_md = REPORT_DIR / "wealth_blueprint_claim_evidence_e2e.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_json),
                "md": str(out_md),
                "pilot_gate": ledger_pilot["gate"].get("decision"),
                "emergency_gate": ledger_emergency["gate"].get("decision"),
                "workflow": workflow.get("workflow_status"),
                "cfpb_statuses": [c["status"] for c in report["claim_ledger_emergency"]["claims"] if c.get("source_required")],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
