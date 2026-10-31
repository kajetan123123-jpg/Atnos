from __future__ import annotations

from agent.models import ToolCallResult


class FailureRecoveryPlanner:
    """Turns tool failures into concrete next actions instead of vague errors."""

    def plan_recovery(self, tool_results: tuple[ToolCallResult, ...]) -> tuple[str, ...]:
        actions: list[str] = []
        for result in tool_results:
            if result.success:
                continue
            actions.extend(self._actions_for_result(result))
        return tuple(dict.fromkeys(actions))

    def _actions_for_result(self, result: ToolCallResult) -> tuple[str, ...]:
        status = result.status
        tool_name = result.tool_name
        error = (result.error or "").lower()
        payload_status = str(result.result.get("status") or "").lower()

        if tool_name.startswith("ubuntu_") or tool_name == "ubuntu_run":
            if "auth" in error or "permission" in error:
                return ("Sprawdź klucz SSH i uprawnienia użytkownika `atnos`.",)
            if "timeout" in error:
                return ("Uruchom krótszą komendę diagnostyczną na Ubuntu i zwiększ timeout tylko jeśli proces faktycznie trwa długo.",)
            return ("Sprawdź `data/logs/tool_calls.jsonl` oraz zdalny `/home/atnos/atnos/logs/commands.log`.",)

        if tool_name == "browser_open":
            return (
                "Sprawdź, czy `BROWSER_ENABLED=true` i czy Playwright ma zainstalowane przeglądarki.",
                "Jeśli strona wymaga loginu/CAPTCHA/2FA, zatrzymaj workflow jako `HUMAN_REQUIRED`.",
            )

        if tool_name == "content_short":
            if payload_status == "runtime_context_required":
                return ("Uruchom short pipeline z kontekstem backendu: session + provider_registry + plugin_registry.",)
            if "piper" in error or "whisper" in error or "ffmpeg" in error:
                return ("Sprawdź readiness Piper, whisper.cpp i FFmpeg przed kolejną próbą renderu.",)
            return ("Sprawdź `data/logs/short_pipeline.jsonl` i artefakty w `data/shorts`.",)

        if tool_name == "memory_write":
            return ("Sprawdź katalog `data/memory` i uprawnienia zapisu na F:.",)

        return (f"Dodaj recovery handler dla narzędzia `{tool_name}` albo przekaż zadanie do zwykłego Chat flow.",)
