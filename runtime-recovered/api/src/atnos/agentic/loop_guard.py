from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class LoopVerdict:
    should_stop: bool
    reason: str
    severity: Literal["ok", "warn", "stop"] = "ok"


class LoopGuard:
    def __init__(
        self,
        *,
        max_turns: int = 15,
        max_same_tool_consecutive: int = 2,
        max_identical_calls: int = 1,
    ) -> None:
        self.max_turns = max(1, max_turns)
        self.max_same_tool_consecutive = max(1, max_same_tool_consecutive)
        self.max_identical_calls = max(1, max_identical_calls)
        self._tool_names: list[str] = []
        self._call_hashes: list[str] = []
        self._turns = 0

    def register_turn(self) -> LoopVerdict:
        self._turns += 1
        if self._turns > self.max_turns:
            return LoopVerdict(True, f"Exceeded max turns: {self.max_turns}", "stop")
        return LoopVerdict(False, "ok", "ok")

    def register_tool_call(self, tool_name: str, args: dict[str, Any]) -> LoopVerdict:
        self._tool_names.append(tool_name)
        call_hash = self._stable_call_hash(tool_name, args)
        self._call_hashes.append(call_hash)

        recent_names = self._tool_names[-self.max_same_tool_consecutive :]
        if (
            len(recent_names) == self.max_same_tool_consecutive
            and len(set(recent_names)) == 1
        ):
            return LoopVerdict(True, f"Tool '{tool_name}' repeated too many times.", "stop")

        if self._call_hashes.count(call_hash) > self.max_identical_calls:
            return LoopVerdict(True, f"Identical tool call repeated: {tool_name}", "stop")

        if self._has_abab_cycle():
            return LoopVerdict(True, "Detected repeated A/B tool cycle.", "stop")

        if self._has_probe_spam():
            return LoopVerdict(True, "Probe tools used without progress.", "stop")

        return LoopVerdict(False, "ok", "ok")

    @staticmethod
    def _stable_call_hash(tool_name: str, args: dict[str, Any]) -> str:
        ignored = {"timestamp", "request_id"}
        filtered = {key: value for key, value in args.items() if key not in ignored}
        return f"{tool_name}:{json.dumps(filtered, ensure_ascii=False, sort_keys=True, default=str)}"

    def _has_abab_cycle(self) -> bool:
        if len(self._tool_names) < 4:
            return False
        recent = self._tool_names[-4:]
        return (
            recent[0] == recent[2]
            and recent[1] == recent[3]
            and recent[0] != recent[1]
        )

    def _has_probe_spam(self) -> bool:
        probe_tools = {"self_state", "browser_tabs"}
        if len(self._tool_names) < 3:
            return False
        recent = self._tool_names[-3:]
        return all(name in probe_tools for name in recent)
