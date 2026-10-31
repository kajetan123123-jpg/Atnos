from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from atnos.agentic.loop_guard import LoopGuard
from atnos.llm.base import LLMBackend, ToolCall, LLMRunCancelled
from atnos.core.logging import redact_secrets
from atnos.providers.base import ProviderExecutionError
from atnos.tools.executor import ToolExecutor
from atnos.tools.registry import ToolRegistry, default_tool_registry, discover_builtin_tools

StreamEventCallback = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class AgentContext:
    system_prompt: str
    backend: LLMBackend
    registry: ToolRegistry = default_tool_registry
    max_turns: int = 15
    required_tool_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolCallOutcome:
    name: str
    arguments: dict[str, Any]
    id: str | None
    status: str
    summary: str
    data: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AgentResult:
    final_response: str
    turns_used: int
    tool_calls_made: tuple[ToolCall, ...]
    stopped_reason: str
    total_latency_s: float
    tool_outcomes: tuple[ToolCallOutcome, ...] = ()


class OrchestratorAgent:
    def __init__(self, *, executor: ToolExecutor | None = None) -> None:
        discover_builtin_tools()
        self.executor = executor

    async def run(
        self,
        user_message: str,
        context: AgentContext,
        *,
        history_messages: list[dict[str, Any]] | None = None,
        on_event: StreamEventCallback | None = None,
    ) -> AgentResult:
        started = perf_counter()
        executor = self.executor or ToolExecutor(context.registry)
        loop_guard = LoopGuard(max_turns=context.max_turns)
        messages: list[dict[str, Any]] = []
        for item in history_messages or []:
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant", "system"} or not content:
                continue
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        tools = context.registry.get_ollama_tools()
        tool_calls_made: list[ToolCall] = []
        tool_outcomes: list[ToolCallOutcome] = []
        tool_request_reminded = False
        argument_rejections: set[str] = set()
        execution_attempts: set[str] = set()

        def emit(event_type: str, **payload: Any) -> None:
            if on_event is None:
                return
            try:
                on_event({"type": event_type, **payload})
            except LLMRunCancelled:
                raise
            except Exception:
                pass

        emit("status", stage="thinking", title="Atnos myśli", detail="Analizuję polecenie…")

        for turn_index in range(1, context.max_turns + 1):
            turn_verdict = loop_guard.register_turn()
            if turn_verdict.should_stop:
                return self._result(
                    final_response="",
                    turns_used=turn_index - 1,
                    tool_calls_made=tool_calls_made,
                    tool_outcomes=tool_outcomes,
                    stopped_reason="max_turns",
                    started=started,
                )

            emit("status", stage="thinking", title="Atnos myśli", detail=f"Tura {turn_index}…")

            def on_token(delta: str, *, _turn: int = turn_index) -> None:
                emit("token", text=delta, turn=_turn)

            try:
                response = await self._chat_complete(
                    context,
                    messages=messages,
                    tools=tools,
                    on_token=on_token if on_event is not None and all(name in {call.name for call in tool_calls_made}
                        and name not in argument_rejections for name in context.required_tool_names) else None,
                )
            except ProviderExecutionError as exc:
                if not tool_outcomes:
                    raise
                # A later provider failure cannot erase already executed tools.
                # Return their real receipts to the ordinary partial-completion
                # formatter; never replay the write or infer a missing result.
                return self._result(
                    final_response='Dalsza praca modelu została przerwana: '
                        + str(redact_secrets(str(exc)))[:600],
                    turns_used=turn_index, tool_calls_made=tool_calls_made,
                    tool_outcomes=tool_outcomes, stopped_reason='provider_error', started=started,
                )
            if not response.tool_calls:
                missing_tools = [name for name in context.required_tool_names
                    if name not in {call.name for call in tool_calls_made} or name in argument_rejections]
                if missing_tools and not tool_request_reminded and turn_index < context.max_turns:
                    tool_request_reminded = True
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": "The requested operation is incomplete. Use a real tool call for the remaining operation now, with its exact schema and name: " + ", ".join(missing_tools) + ". Do not repeat completed steps, describe or simulate the call. This is the only reminder."})
                    continue
                emit("status", stage="answering", title="Atnos odpowiada", detail="Składam finalną odpowiedź…")
                return self._result(
                    final_response=response.content,
                    turns_used=turn_index,
                    tool_calls_made=tool_calls_made,
                    tool_outcomes=tool_outcomes,
                    stopped_reason="completed",
                    started=started,
                )

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [self._tool_call_to_message_payload(call) for call in response.tool_calls],
            }
            messages.append(assistant_message)

            for tool_call in response.tool_calls:
                tool_calls_made.append(tool_call)
                # Invalid arguments produce feedback without any operation.
                # Do not count them as an executed repeat: permit a corrected
                # call while retaining max-turn and actual-call loop limits.
                validation_error = executor.validate_tool_call(tool_call)
                if validation_error is not None and tool_call.name not in execution_attempts:
                    # Schema rejection is proof execution did not begin. It
                    # must not satisfy a requested operation merely by name.
                    argument_rejections.add(tool_call.name)
                elif validation_error is None:
                    # A runtime failure may have committed a side effect.
                    # Never turn it into a blind schema-recovery reminder.
                    argument_rejections.discard(tool_call.name)
                    execution_attempts.add(tool_call.name)
                verdict = loop_guard.register_tool_call(tool_call.name, tool_call.arguments) if validation_error is None else None
                if verdict is not None and verdict.should_stop:
                    recovery = self._loop_recovery_response(
                        tool_outcomes=tool_outcomes,
                        reason=verdict.reason,
                    )
                    return self._result(
                        final_response=recovery,
                        turns_used=turn_index,
                        tool_calls_made=tool_calls_made,
                        tool_outcomes=tool_outcomes,
                        stopped_reason="loop_detected",
                        started=started,
                    )
                emit(
                    "tool_start",
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                    turn=turn_index,
                )
                result = await executor.execute_tool_call(tool_call)
                tool_status = "ok"
                tool_summary = ""
                if hasattr(result, "success") and not bool(result.success):
                    tool_status = "error"
                    tool_summary = str(getattr(result, "error", None) or "Tool failed")[:240]
                if isinstance(getattr(result, "data", None), dict):
                    data = result.data
                    tool_status = str(data.get("status") or tool_status)
                    tool_summary = str(
                        data.get("summary")
                        or data.get("label")
                        or data.get("query")
                        or data.get("message")
                        or tool_summary
                        or ""
                    )[:240]
                tool_outcomes.append(
                    ToolCallOutcome(
                        name=tool_call.name,
                        arguments=dict(tool_call.arguments or {}),
                        id=tool_call.id,
                        status=tool_status,
                        summary=tool_summary,
                        data=result.data if isinstance(result.data, dict) and result.success else None,
                    )
                )
                emit(
                    "tool_result",
                    name=tool_call.name,
                    status=tool_status,
                    summary=tool_summary,
                    turn=turn_index,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_call.name,
                        "tool_call_id": tool_call.id or tool_call.name,
                        "content": result.to_tool_message_content(literal_text=tool_call.name == 'fs_read'),
                    }
                )
                if (
                    tool_call.name == "ai_delegate_background"
                    and result.success and isinstance(result.data, dict)
                    and result.data.get("status") == "started_background"
                    and isinstance(result.data.get("job_token"), str)
                    and all(name in {call.name for call in tool_calls_made} for name in context.required_tool_names)
                ):
                    # Scheduling is acknowledged from the receipt. Another chat
                    # generation would compete with the background coding model
                    # for GPU memory and cannot certify that job's completion.
                    return self._result(
                        final_response=f"Zleciłem zadanie w tle: {result.data['job_token']}. Wynik nie jest jeszcze gotowy.",
                        turns_used=turn_index, tool_calls_made=tool_calls_made,
                        tool_outcomes=tool_outcomes, stopped_reason="completed", started=started,
                    )
                if self._is_hard_stop_confirmation(result):
                    summary = ""
                    if isinstance(result.data, dict):
                        summary = str(result.data.get("summary") or result.data.get("reason") or "")
                    final = (
                        "Wymagane jest Twoje potwierdzenie przed wykonaniem inwazyjnej akcji.\n"
                        f"{summary}\n"
                        "Napisz w czacie: potwierdzam"
                    ).strip()
                    return self._result(
                        final_response=final,
                        turns_used=turn_index,
                        tool_calls_made=tool_calls_made,
                        tool_outcomes=tool_outcomes,
                        stopped_reason="needs_confirmation",
                        started=started,
                    )

            from atnos.services.chat_tool_focus import format_obsidian_read, obsidian_read_complete
            partial_reads = [item.data for item in tool_outcomes if item.name == 'obsidian_read'
                             and isinstance(item.data, dict) and not obsidian_read_complete(item.data)]
            if partial_reads:
                # A bounded excerpt is a real result, not a complete note.
                # Stop instead of repeating the same read or trusting model prose.
                return self._result(final_response='\n\n'.join(format_obsidian_read(item) for item in partial_reads),
                    turns_used=turn_index, tool_calls_made=tool_calls_made, tool_outcomes=tool_outcomes,
                    stopped_reason='incomplete', started=started)
            successful = {item.name for item in tool_outcomes if item.status in {"ok", "written", "queued_for_manual_execution"} and isinstance(item.data, dict)}
            if (context.required_tool_names
                    and set(context.required_tool_names).issubset({"obsidian_search", "obsidian_read", "obsidian_write", "ai_queue_tasks"})
                    and set(context.required_tool_names).issubset(successful)):
                # Finish from real receipts after the full batch, even when the
                # final required operation was search following a saved edit.
                reads = [item.data for item in tool_outcomes if item.name == "obsidian_read" and item.status == "ok" and isinstance(item.data, dict)]
                text = "Operacja potwierdzona przez narzędzie."
                if reads and "obsidian_write" not in context.required_tool_names:
                    text = "\n\n".join(format_obsidian_read(item) for item in reads)
                return self._result(final_response=text, turns_used=turn_index,
                    tool_calls_made=tool_calls_made, tool_outcomes=tool_outcomes,
                    stopped_reason="completed", started=started)

        return self._result(
            final_response="",
            turns_used=context.max_turns,
            tool_calls_made=tool_calls_made,
            tool_outcomes=tool_outcomes,
            stopped_reason="max_turns",
            started=started,
        )

    @staticmethod
    async def _chat_complete(
        context: AgentContext,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_token: Callable[[str], None] | None,
    ):
        backend = context.backend
        try:
            return await backend.chat_complete(
                messages=messages,
                system=context.system_prompt,
                tools=tools,
                on_token=on_token,
            )
        except TypeError:
            # Backends that do not accept on_token yet.
            return await backend.chat_complete(
                messages=messages,
                system=context.system_prompt,
                tools=tools,
            )

    @staticmethod
    def _tool_call_to_message_payload(tool_call: ToolCall) -> dict[str, Any]:
        return {
            "id": tool_call.id or tool_call.name,
            "type": "function",
            "function": {
                "name": tool_call.name,
                "arguments": tool_call.raw_arguments or tool_call.arguments,
            },
        }

    @staticmethod
    def _is_hard_stop_confirmation(result: Any) -> bool:
        data = getattr(result, "data", None)
        return isinstance(data, dict) and data.get("status") == "needs_confirmation"

    @staticmethod
    def _loop_recovery_response(
        *,
        tool_outcomes: list[ToolCallOutcome],
        reason: str,
    ) -> str:
        if not tool_outcomes:
            return (
                "Wykryłem pętlę narzędzi i przerwałem wykonanie, zanim powstała odpowiedź.\n"
                f"Powód: {reason}"
            )
        last = tool_outcomes[-1]
        names = []
        for item in tool_outcomes:
            if item.name not in names:
                names.append(item.name)
        lines = [
            "Wykryłem powtarzanie tego samego narzędzia i zatrzymałem pętlę.",
            f"Powód: {reason}",
            f"Użyte narzędzia: {', '.join(names)}.",
        ]
        if last.summary:
            lines.append(f"Ostatni wynik (`{last.name}`): {last.summary}")
        elif last.status:
            lines.append(f"Ostatni status (`{last.name}`): {last.status}")
        lines.append("Napisz konkretniej, czego potrzebujesz — nie będę powtarzał tego samego tool calla.")
        return "\n".join(lines)

    @staticmethod
    def _result(
        *,
        final_response: str,
        turns_used: int,
        tool_calls_made: list[ToolCall],
        tool_outcomes: list[ToolCallOutcome],
        stopped_reason: str,
        started: float,
    ) -> AgentResult:
        return AgentResult(
            final_response=final_response,
            turns_used=turns_used,
            tool_calls_made=tuple(tool_calls_made),
            tool_outcomes=tuple(tool_outcomes),
            stopped_reason=stopped_reason,
            total_latency_s=perf_counter() - started,
        )
