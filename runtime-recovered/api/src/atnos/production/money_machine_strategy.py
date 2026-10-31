from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import utc_now_iso
from atnos.procurement.autonomy_planner import AutonomyPlanner
from atnos.procurement.smart_spending import SmartSpendingService
from atnos.production.money_machine import (
    MoneyMachineActionService,
    MoneyMachineStatusService,
    build_money_machine_operator_summary,
)
from atnos.production.readiness import ShortProductionReadinessService


class MoneyMachineStrategyService:
    """Buduje bezpieczny raport operacyjny: jak Atnos ma zarabiać i co blokuje start."""

    def __init__(self, settings: Settings, *, session: Session | None = None) -> None:
        self.settings = settings
        self.session = session

    async def build(
        self,
        *,
        account_id: str | None = None,
        include_live_checks: bool = False,
        send_gmail: bool = False,
    ) -> dict[str, Any]:
        """Zbuduj raport, zapisz artefakty i opcjonalnie wyślij go przez Gmail OAuth."""
        generated_at = utc_now_iso()
        smart_spending = SmartSpendingService(self.settings)
        financial_awareness = smart_spending.awareness()
        card_access = smart_spending.card_access_status()
        readiness = await self._safe_readiness(include_live_checks=include_live_checks)
        money_status = self._safe_money_status(account_id=account_id)
        run_next = self._safe_run_next(account_id=account_id)
        operator_summary = build_money_machine_operator_summary(money_status, run_next)
        autonomy_plan = self._safe_autonomy_plan(account_id=account_id)
        blockers = self._blockers(
            readiness=readiness,
            money_status=money_status,
            operator_summary=operator_summary,
            financial_awareness=financial_awareness,
            card_access=card_access,
        )
        samples = self._samples()
        revenue_plan = self._revenue_plan(readiness=readiness, money_status=money_status)
        payload: dict[str, Any] = {
            "status": "ready_to_operate_with_supervision" if not blockers["critical"] else "needs_work",
            "generated_at": generated_at,
            "account_id": account_id,
            "financial_awareness": financial_awareness,
            "card_access": card_access,
            "spending_rules": self._spending_rules(),
            "revenue_plan": revenue_plan,
            "samples": samples,
            "readiness": self._compact_readiness(readiness),
            "money_machine": {
                "status": money_status.get("status"),
                "operator_summary": operator_summary,
                "run_next": {
                    "status": run_next.get("status"),
                    "planned_count": len(run_next.get("planned") or []),
                    "blocked_count": len(run_next.get("blocked") or []),
                },
            },
            "autonomy_plan": self._compact_autonomy_plan(autonomy_plan),
            "blockers": blockers,
            "next_best_actions": self._next_best_actions(blockers=blockers, operator_summary=operator_summary),
            "safety_policy": self._safety_policy(),
        }
        artifacts = self.write_report(payload)
        payload["artifacts"] = artifacts
        payload["gmail"] = self._dispatch_final_gmail_if_ready(
            payload=payload,
            markdown_path=Path(artifacts["markdown_path"]),
            send_gmail=send_gmail,
        )
        return payload

    def write_report(self, payload: dict[str, Any]) -> dict[str, str]:
        """Zapisz raport JSON i Markdown do data/reports."""
        reports_dir = Path(self.settings.data_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(payload.get("generated_at") or utc_now_iso()).replace(":", "").replace("-", "")
        safe_stamp = stamp.replace("+0000", "Z").replace(".", "_")
        json_path = reports_dir / f"money_machine_strategy_{safe_stamp}.json"
        markdown_path = reports_dir / f"money_machine_strategy_{safe_stamp}.md"
        markdown = self.render_markdown(payload)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(markdown, encoding="utf-8")
        return {
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
        }

    def dispatch_gmail(self, *, markdown_path: Path) -> dict[str, Any]:
        """Wyślij raport właścicielowi, jeśli Gmail ma realne write-scope."""
        if self.session is None:
            return {"status": "blocked", "message": "No database session was provided."}
        from atnos.repositories.user_repository import UserRepository
        from atnos.services.gmail_connector_service import GmailConnectorService

        owner = UserRepository(self.session).get_owner()
        owner_email = str(getattr(owner, "email", "") or "").strip()
        if not owner_email:
            return {"status": "blocked", "message": "Owner email is not configured."}
        gmail = GmailConnectorService(self.session, self.settings)
        status = gmail.get_status()
        if not status.live_write_enabled:
            return {
                "status": "blocked",
                "message": "Gmail OAuth write access is not configured.",
                "oauth_status": status.oauth_status,
                "connected": status.connected,
                "live_write_enabled": status.live_write_enabled,
            }
        body = markdown_path.read_text(encoding="utf-8")
        draft = gmail.draft_message(
            to=owner_email,
            subject="[Atnos] Money Machine - plan i próbki",
            body=body,
        )
        sent = gmail.send_message(draft_id=str(draft["draft_id"]))
        return {
            "status": "sent",
            "to": owner_email,
            "draft": draft,
            "sent": sent,
        }

    def _dispatch_final_gmail_if_ready(
        self,
        *,
        payload: dict[str, Any],
        markdown_path: Path,
        send_gmail: bool,
    ) -> dict[str, Any]:
        """Wyślij finalny raport tylko wtedy, gdy maszynka realnie przeszła bramki gotowości."""
        if not send_gmail:
            return {"status": "skipped", "message": "Gmail dispatch was not requested."}
        if self.session is None:
            return {"status": "skipped", "message": "No database session was provided."}
        if payload.get("status") != "ready_to_operate_with_supervision":
            return {
                "status": "blocked_not_ready",
                "message": "Final Gmail report is blocked until Atnos has no critical money-machine blockers.",
                "critical_blockers": list((payload.get("blockers") or {}).get("critical") or []),
                "markdown_path": str(markdown_path),
            }
        return self.dispatch_gmail(markdown_path=markdown_path)

    async def _safe_readiness(self, *, include_live_checks: bool) -> dict[str, Any]:
        try:
            return await ShortProductionReadinessService(self.settings, session=self.session).check(
                include_live_checks=include_live_checks,
            )
        except Exception as exc:
            return {
                "status": "error",
                "ready": False,
                "blocking": ["readiness_check_failed"],
                "message": f"{type(exc).__name__}: {exc}",
            }

    def _safe_money_status(self, *, account_id: str | None) -> dict[str, Any]:
        try:
            return MoneyMachineStatusService(self.settings, session=self.session).build(
                account_id=account_id,
                include_oauth=self.session is not None,
            )
        except Exception as exc:
            return {
                "status": "error",
                "global_blockers": ["money_machine_status_failed"],
                "next_actions": [{"scope": "global", "action": "repair_money_machine_status"}],
                "totals": {},
                "accounts": [],
                "message": f"{type(exc).__name__}: {exc}",
            }

    def _safe_run_next(self, *, account_id: str | None) -> dict[str, Any]:
        if self.session is None:
            return {"status": "skipped", "planned": [], "blocked": [], "message": "No session provided."}
        try:
            return MoneyMachineActionService(self.settings, session=self.session).run_next(
                account_id=account_id,
                max_uploads=3,
                per_account_limit=1,
                execute=False,
                approved_private_upload=False,
            )
        except Exception as exc:
            return {
                "status": "error",
                "planned": [],
                "blocked": [{"action": "run_next_failed", "reason": f"{type(exc).__name__}: {exc}"}],
            }

    def _safe_autonomy_plan(self, *, account_id: str | None) -> dict[str, Any]:
        try:
            return AutonomyPlanner(self.settings, session=self.session).build_plan(
                account_id=account_id,
                include_optional=True,
            )
        except Exception as exc:
            return {
                "status": "error",
                "required_missing": [],
                "optional_missing": [],
                "financial_awareness": SmartSpendingService(self.settings).awareness(),
                "message": f"{type(exc).__name__}: {exc}",
            }

    def render_markdown(self, payload: dict[str, Any]) -> str:
        """Zamień raport na czytelnego maila operatorskiego."""
        financial = payload["financial_awareness"]
        card = payload["card_access"]
        blockers = payload["blockers"]
        samples = payload["samples"]
        lines = [
            "# Atnos Money Machine — raport operacyjny",
            "",
            f"Data: {payload['generated_at']}",
            f"Status: {payload['status']}",
            "",
            "## Decyzja operacyjna",
            "- Atnos ma działać jako nadzorowana maszyna produkcyjna: research → prywatne próbki → QA → decyzja operatora.",
            "- Publiczny upload, realne zakupy i realny trading pozostają zablokowane, dopóki bramki jakości i zgody nie są spełnione.",
            "- Najpierw dowozimy powtarzalną jakość shortów i raportów, dopiero potem skalujemy publikację oraz wydatki.",
            "",
            "## Budżet i zasady wydatków",
            f"- Gotówka operacyjna: {financial['current_cash_pln']} {financial['currency']}",
            f"- Zarobione łącznie: {financial['earned_total_pln']} {financial['currency']}",
            f"- Wydane łącznie: {financial['spent_total_pln']} {financial['currency']}",
            f"- Dostęp do karty: {'TAK' if card['can_use_card'] else 'NIE'} ({card['payment_execution']})",
            "- Atnos nie zapisuje danych karty, CVV, PIN, kodów SMS ani loginów bankowych.",
            "- Każdy płatny zakup ma iść jako ręczny checkout z opisem celu, kosztu i oczekiwanego wpływu na produkcję.",
            "",
            "## Plan zarabiania",
        ]
        for item in payload["revenue_plan"]:
            lines.append(f"- {item['name']}: {item['status']} - {item['how_it_earns']}")
        lines.extend(["", "## Blokery"])
        if blockers["critical"]:
            lines.extend(f"- CRITICAL: {item}" for item in blockers["critical"])
        if blockers["warnings"]:
            lines.extend(f"- WARNING: {item}" for item in blockers["warnings"])
        if not blockers["critical"] and not blockers["warnings"]:
            lines.append("- Brak krytycznych blokerów w raporcie.")
        lines.extend(["", "## Próbki shortów — 3 kanały"])
        for channel in samples["shorts"]:
            lines.append(f"### {channel['channel_name']} ({channel['language']})")
            lines.append(f"- Głos: {channel['voice']}")
            lines.append(f"- Zasada wizualna: {channel['visual_rule']}")
            for idea in channel["ideas"]:
                beats = " → ".join(idea["script_beats"])
                lines.append(f"- {idea['title']} | Hook: {idea['hook']} | Wizual: {idea['visual_direction']} | Beat: {beats}")
        lines.extend(["", "## Konto twórcy / synthetic creator"])
        for creator in samples["synthetic_creators"]:
            lines.append(
                f"- {creator['name']}: {creator['positioning']} | granice: {creator['boundaries']} | próbka: {creator['sample_post']}"
            )
        lines.extend(["", "## Portfel inwestycyjny"])
        for item in samples["investment"]:
            lines.append(f"- {item['name']}: {item['role']} | ograniczenie: {item['boundary']} | przykład: {item['sample_output']}")
        lines.extend(["", "## Produkty dodatkowe"])
        for item in samples["other"]:
            lines.append(f"- {item['name']}: {item['sample_offer']}")
        lines.extend(["", "## Następne kroki"])
        for action in payload["next_best_actions"]:
            lines.append(f"- {action}")
        lines.extend(["", "## Zasady bezpieczeństwa"])
        for item in payload["safety_policy"]:
            lines.append(f"- {item}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _compact_readiness(readiness: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": readiness.get("status") or readiness.get("overall"),
            "full_pipeline_ready": bool(readiness.get("full_pipeline_ready")),
            "private_upload_ready": bool(readiness.get("private_upload_ready")),
            "public_publish_ready": bool(readiness.get("public_publish_ready")),
            "blocking": list(readiness.get("blocking") or []),
            "warnings": list(readiness.get("warnings") or []),
            "next_actions": list(readiness.get("next_actions") or []),
        }

    @staticmethod
    def _compact_autonomy_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": plan.get("status"),
            "required_missing_count": len(plan.get("required_missing") or []),
            "optional_missing_count": len(plan.get("optional_missing") or []),
            "purchase_candidates_count": len(plan.get("purchase_candidates") or []),
            "access_tasks_count": len(plan.get("access_tasks") or []),
            "blocked_actions": list(plan.get("blocked_actions") or []),
            "financial_awareness": plan.get("financial_awareness"),
        }

    @staticmethod
    def _blockers(
        *,
        readiness: dict[str, Any],
        money_status: dict[str, Any],
        operator_summary: dict[str, Any],
        financial_awareness: dict[str, Any],
        card_access: dict[str, Any],
    ) -> dict[str, list[str]]:
        critical = list(dict.fromkeys(str(item) for item in readiness.get("blocking") or [] if str(item).strip()))
        critical.extend(str(item) for item in money_status.get("global_blockers") or [] if str(item).strip())
        warnings = []
        if not bool(card_access.get("can_use_card")):
            warnings.append("direct_card_use_forbidden_manual_checkout_only")
        if float(financial_awareness.get("current_cash_pln") or 0.0) <= 0:
            critical.append("no_operating_cash_available")
        if not bool(readiness.get("public_publish_ready")):
            warnings.append("public_publish_not_ready_or_disabled")
        if operator_summary.get("next_action"):
            warnings.append(f"operator_next_action:{operator_summary['next_action']}")
        return {
            "critical": list(dict.fromkeys(critical)),
            "warnings": list(dict.fromkeys(warnings)),
        }

    @staticmethod
    def _spending_rules() -> list[str]:
        return [
            "Wydawaj wyłącznie z dostępnego salda SmartSpending.",
            "Nigdy nie zapisuj ani nie używaj surowych danych karty, CVV, PIN-u, loginu bankowego, kodów SMS ani kodów 3DS.",
            "Płatne narzędzia są tylko w trybie ręcznego checkoutu; Atnos może przygotować prośbę i wysłać uzasadnienie.",
            "Każdy wydatek musi mieć powód biznesowy i wpis w ledgerze.",
            "Skaluj wydatki dopiero po realnym przychodzie albo ręcznym doładowaniu przez właściciela.",
        ]

    @staticmethod
    def _safety_policy() -> list[str]:
        return [
            "Bez obietnic gwarantowanego zysku.",
            "Bez publicznego uploadu bez QA gate i jawnej polityki publikacji.",
            "Bez real-money tradingu; inwestycje są tylko research/content/read-only monitoring, dopóki nie istnieje osobna polityka brokera.",
            "Synthetic creator: treści non-explicit, bez podszywania się pod realne osoby, z kontrolą człowieka przed publikacją.",
            "Treści dziecięce i edukacyjne muszą być age-appropriate, bez unsafe challenge i bez zbierania danych dzieci.",
        ]

    @staticmethod
    def _revenue_plan(*, readiness: dict[str, Any], money_status: dict[str, Any]) -> list[dict[str, str]]:
        public_ready = bool(readiness.get("public_publish_ready"))
        private_ready = bool(readiness.get("private_upload_ready"))
        status = str(money_status.get("status") or "unknown")
        return [
            {
                "lane": "youtube_shorts",
                "name": "3 kanały YouTube Shorts / TikTok / Instagram",
                "status": "production_ready_private_review" if private_ready else f"blocked:{status}",
                "how_it_earns": "Najpierw prywatne próbki i retencja. Potem monetyzacja reklamami, afiliacją, lead magnetami i seriami formatów.",
            },
            {
                "lane": "synthetic_creator",
                "name": "Konto twórcy / synthetic creator",
                "status": "concept_and_sample_ready_human_review_required",
                "how_it_earns": "Bezpieczne postacie fikcyjne, testy sponsorów, płatne assety i paczki contentowe; bez automatyki explicit.",
            },
            {
                "lane": "investment_content",
                "name": "Portfel inwestycyjny i content finansowy",
                "status": "research_only_no_trading",
                "how_it_earns": "Materiały edukacyjne, watchlisty, porównania narzędzi i afiliacja; bez automatycznych transakcji realnymi pieniędzmi.",
            },
            {
                "lane": "digital_products",
                "name": "Produkty cyfrowe low-ticket",
                "status": "draft_ready_after_first_audience_signal",
                "how_it_earns": "Checklisty, prompt packi, plany kanałów i szablony arkuszy po potwierdzeniu popytu organicznego.",
            },
            {
                "lane": "public_upload",
                "name": "Publiczna publikacja",
                "status": "ready" if public_ready else "supervised_or_disabled",
                "how_it_earns": "Tylko po prywatnym QA, przeglądzie właściciela i gotowości platform.",
            },
        ]

    @staticmethod
    def _samples() -> dict[str, Any]:
        return {
            "shorts": [
                {
                    "channel_key": "personal_finance",
                    "channel_name": "Personal Finance Shorts EN",
                    "language": "EN",
                    "voice": "calm but tense, clear male/female premium narrator",
                    "visual_rule": "Every scene must show a visible money mechanism: cashflow, debt, subscriptions, charts or spending behavior.",
                    "ideas": [
                        {
                            "title": "The Quiet Money Trap Keeping You Broke",
                            "hook": "If your income went up but your account still feels empty, this is probably why.",
                            "visual_direction": "Cold apartment desk, bank app blur, expense bars snapping upward, red leak animation.",
                            "script_beats": ["income rises", "lifestyle creep hides", "one rule fixes it", "CTA: track one leak tonight"],
                        },
                        {
                            "title": "Most People Budget Backwards",
                            "hook": "A budget fails when it starts with bills instead of behavior.",
                            "visual_direction": "Split-screen receipts vs habit loop, animated cash flow, bold 3-word subtitles.",
                            "script_beats": ["bad budget order", "behavior trigger", "reverse rule", "CTA: automate first transfer"],
                        },
                    ],
                },
                {
                    "channel_key": "dark_history",
                    "channel_name": "Dark History Shorts EN",
                    "language": "EN",
                    "voice": "suspense narrator, low pace, controlled tension",
                    "visual_rule": "Every scene must include period-specific evidence: maps, letters, archive stamps, artifacts or locations tied to the story.",
                    "ideas": [
                        {
                            "title": "The Message That Started a Panic",
                            "hook": "One sentence crossed a border, and thousands of people changed their plans overnight.",
                            "visual_direction": "Old paper, map line animation, candle light, typewriter close-ups, fast first cut.",
                            "script_beats": ["single message", "why people believed it", "consequence", "unanswered question"],
                        },
                        {
                            "title": "The Village That Vanished From Records",
                            "hook": "The strange part is not that people left. It is what disappeared after them.",
                            "visual_direction": "Foggy road, empty houses, archive stamp, slow zoom into missing registry line.",
                            "script_beats": ["place setup", "record gap", "theory contrast", "final eerie detail"],
                        },
                    ],
                },
                {
                    "channel_key": "ai_absurd",
                    "channel_name": "AI Absurd / Brainrot Safe EN",
                    "language": "EN",
                    "voice": "fast comic narrator, bright but not chaotic",
                    "visual_rule": "Every scene must turn the joke into an object or action, not a generic abstract background.",
                    "ideas": [
                        {
                            "title": "AI Tried to Run a Lemonade Stand",
                            "hook": "It invented a loyalty program before it bought lemons.",
                            "visual_direction": "Colorful mascot, dashboard chaos, lemons as charts, quick scene flips every 1.2s.",
                            "script_beats": ["wrong priority", "absurd optimization", "human fix", "punchline"],
                        },
                        {
                            "title": "When a Robot Learns Homework Excuses",
                            "hook": "The excuse was technically perfect and socially disastrous.",
                            "visual_direction": "Classroom-safe cartoon robot, floating excuses, teacher silhouette, clean kid-safe palette.",
                            "script_beats": ["robot output", "why it fails", "human lesson", "safe joke CTA"],
                        },
                    ],
                },
            ],
            "synthetic_creators": [
                {
                    "name": "Nova Vale",
                    "positioning": "fictional tech-fashion host for AI tools, productivity and cinematic product demos",
                    "boundaries": "non-explicit, no real-person imitation, no deceptive identity claims",
                    "sample_post": "I tested three AI tools so you do not waste your weekend. The winner was not the prettiest one.",
                },
                {
                    "name": "Mira Ledger",
                    "positioning": "fictional finance explainer avatar for simple money habits and visual dashboards",
                    "boundaries": "non-explicit, education only, no financial advice, no trading signals",
                    "sample_post": "This tiny money leak looks harmless until you animate it over 12 months.",
                },
                {
                    "name": "Orbit Kid Lab",
                    "positioning": "kid-safe educational animated character for weird science and history facts",
                    "boundaries": "non-explicit, age-appropriate, no data collection from children, no unsafe challenges",
                    "sample_post": "Why did ancient people look at the sky and build giant calendars?",
                },
            ],
            "investment": [
                {
                    "name": "Read-only market watchlist",
                    "role": "Collects macro, ETF and broker-tool signals for educational content and operator review.",
                    "boundary": "No automatic orders, no individual financial advice, no promise of profit.",
                    "sample_output": "Weekly brief: what changed in inflation, rates, USD/PLN and broad ETF momentum.",
                },
                {
                    "name": "Investment education content lane",
                    "role": "Turns research into simple Shorts, checklists and comparison scripts.",
                    "boundary": "Education only; Atnos can explain risk but cannot decide what the owner should buy.",
                    "sample_output": "Short idea: The hidden fee that eats small portfolios before they grow.",
                },
            ],
            "other": [
                {
                    "name": "Faceless Shorts checklist",
                    "type": "digital_product",
                    "sample_offer": "A one-page QA checklist for hooks, visuals, captions and upload metadata.",
                },
                {
                    "name": "Tool comparison content",
                    "type": "affiliate_or_sponsor_candidate",
                    "sample_offer": "Honest comparison videos once real usage data exists.",
                },
            ],
        }

    @staticmethod
    def _next_best_actions(*, blockers: dict[str, list[str]], operator_summary: dict[str, Any]) -> list[str]:
        actions: list[str] = []
        next_action = str(
            operator_summary.get("next_operator_action")
            or operator_summary.get("next_action")
            or ""
        ).strip()
        if next_action:
            actions.append(next_action)
        all_blockers = blockers["critical"] + blockers["warnings"]
        if "direct_card_use_forbidden_manual_checkout_only" in all_blockers:
            actions.append("use_procurement_requests_for_paid_tools_instead_of_card_storage")
        if any("voice" in item for item in all_blockers):
            actions.append("stabilize_premium_voice_before_public_upload")
        if any("comfyui" in item or "video" in item or "quality" in item or "visual" in item for item in all_blockers):
            actions.append("run_visual_backend_check_and_regenerate_weak_visuals")
        actions.append("produce_private_samples_only_then_review_qa_report")
        return list(dict.fromkeys(actions))[:8]
