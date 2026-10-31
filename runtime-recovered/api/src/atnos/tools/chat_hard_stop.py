"""Hard-stop policy for high-autonomy Atnos chat tools."""
from __future__ import annotations

import re
from pathlib import Path

SAFE_DELETE_ROOTS = (
    Path(r"E:\Atnos"),
    Path(r"E:\Rolki"),
)

_DESTRUCTIVE_SHELL_PATTERNS = (
    re.compile(r"\bformat(?:-volume|-disk)?\b", re.I),
    re.compile(r"\bclear-disk\b", re.I),
    re.compile(r"\bstop-computer\b", re.I),
    re.compile(r"\brestart-computer\b", re.I),
    re.compile(r"\bshutdown(\.exe)?\b", re.I),
    re.compile(r"\bSend-MailMessage\b", re.I),
    re.compile(r"\bRemove-Item\b.*\s-(Recurse|Force)\b", re.I),
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\bdel\s+/[sf]\b", re.I),
    re.compile(r"\bcipher\s+/w\b", re.I),
    re.compile(r"\bdiskpart\b", re.I),
    re.compile(r"\b(?:stop-process|taskkill(?:\.exe)?|invoke-expression|iex)\b", re.I),
    re.compile(r"-(?:encodedcommand|enc)\b", re.I),
)

_EMAIL_SEND_HINTS = (
    "send-mailmessage",
    "wyslij mail",
    "wyślij mail",
    "send email",
    "gmail send",
    "smtp",
)


def resolve_path(raw: str | Path) -> Path:
    return Path(str(raw)).expanduser().resolve()


def is_under_safe_delete_root(path: Path) -> bool:
    candidate = resolve_path(path)
    for root in SAFE_DELETE_ROOTS:
        try:
            resolved_root = root.resolve()
        except OSError:
            resolved_root = root
        try:
            candidate.relative_to(resolved_root)
            return True
        except ValueError:
            continue
    return False


def classify_delete(path: str | Path) -> tuple[bool, str]:
    """Return (needs_confirmation, reason)."""
    candidate = resolve_path(path)
    if is_under_safe_delete_root(candidate):
        return False, "delete_inside_safe_root"
    return True, "delete_outside_E_Atnos_or_E_Rolki"


def classify_shell_command(command: str) -> tuple[bool, str]:
    text = (command or "").strip()
    if not text:
        return False, "empty"
    lowered = text.casefold()
    for hint in _EMAIL_SEND_HINTS:
        if hint in lowered:
            return True, "email_send"
    for pattern in _DESTRUCTIVE_SHELL_PATTERNS:
        if pattern.search(text):
            return True, f"destructive_shell:{pattern.pattern}"
    remove_matches = re.finditer(
        r"(?:Remove-Item|ri|del|rm|Erase)\s+([^\n|;]+)",
        text,
        flags=re.I,
    )
    for remove_match in remove_matches:
        target = remove_match.group(1).strip()
        target = re.sub(r"^-(?:LiteralPath|Path)\s+", "", target, flags=re.I)
        # A literal absolute path is the only form this guard can establish as safe.
        # Variables, argument lists, wildcards and .NET calls are not inferred safe.
        literal = re.fullmatch(r"(?:'([A-Za-z]:[\\/][^']*)'|\"([A-Za-z]:[\\/][^\"]*)\"|([A-Za-z]:[\\/][^\s,]+))", target)
        if literal is None or any(ch in target for ch in ("$", "*", "?", "`", "(", ")", "{", "}")):
            return True, "delete_target_not_proven_safe"
        resolved = next(group for group in literal.groups() if group is not None)
        needs, reason = classify_delete(resolved)
        if needs:
            return True, reason
    if re.search(r"[\n\r;|&><$`{}()]", text):
        return True, "shell_script_not_proven_safe"
    if re.fullmatch(
        r"(?:Get-(?:Date|Process|Service|Item|ChildItem|Content|ComputerInfo|CimInstance)|Test-Path|Write-Output|pwd|ls|dir|cat|echo)(?:\s+[^\n\r;|&><$`{}()]*)?",
        text, flags=re.I,
    ):
        return False, "allowed"
    # Literal deletes inside the existing safe roots were checked above.
    if re.match(r"^(?:Remove-Item|ri|del|rm|Erase)\s+", text, re.I):
        return False, "delete_inside_safe_root"
    return True, "shell_command_requires_human_review"


def confirmation_phrases() -> frozenset[str]:
    return frozenset(
        {
            "potwierdz",
            "potwierdzam",
            "tak",
            "tak, potwierdzam",
            "tak potwierdzam",
            "ok",
            "ok, potwierdz",
            "ok potwierdzam",
            "confirm",
            "yes",
            "approve",
        }
    )


def looks_like_confirmation(content: str) -> bool:
    normalized = " ".join((content or "").lower().split())
    return normalized in confirmation_phrases()
