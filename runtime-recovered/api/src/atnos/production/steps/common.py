from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.production.models import StepResult
from atnos.services.ollama_model_roles_service import OllamaModelRolesService


class OllamaResourceUnavailable(RuntimeError):
    """A model cannot load; repeating the same request cannot restore memory."""


class RegisteredFieldRequirementsMissing(ValueError):
    """Explicit registered field constraints failed; allow at most three attempts."""


async def ask_ollama_json(settings: Settings, prompt: str, *, timeout_s: float = 30.0,
                          model: str | None = None, schema: dict | None = None,
                          options: dict | None = None, think: bool = False,
                          raw_response_path: Path | None = None,
                          reject_duplicate_keys: bool = False,
                          system_prompt: str | None = None) -> dict[str, Any] | list[Any] | None:
    model = model or (
        settings.ollama_synthesis_model
        or settings.ollama_chat_model
        or settings.ollama_main_model
        or OllamaModelRolesService.DEFAULT_MAIN_TEXT_MODEL
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": schema or "json",
        "think": think,
        # Expire only this completed production request. Never use a separate
        # unload command against an unknown in-flight chat request.
        "keep_alive": 0,
    }
    if system_prompt:
        payload['messages'].insert(0, {'role': 'system', 'content': system_prompt})
    if options is not None:
        payload["options"] = options
    hard_timeout_s = max(1.0, min(float(settings.ollama_timeout_s), timeout_s))
    client_timeout = httpx.Timeout(
        hard_timeout_s,
        connect=min(5.0, hard_timeout_s),
        read=hard_timeout_s,
        write=min(10.0, hard_timeout_s),
        pool=min(5.0, hard_timeout_s),
    )
    try:
        async with asyncio.timeout(hard_timeout_s + 1.0):
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                response = await client.post(f"{settings.ollama_base_url.rstrip('/')}/api/chat", json=payload)
    except TimeoutError as exc:
        raise TimeoutError(f"Ollama JSON call exceeded {hard_timeout_s:.1f}s hard timeout.") from exc
    if response.status_code >= 500:
        try:
            provider_error = str(response.json().get('error', ''))
        except (ValueError, AttributeError):
            provider_error = ''
        if re.search(r'out[ -]of[ -]memory|unable to allocate|cudaMalloc failed|failed to allocate', provider_error, re.I):
            from atnos.core.logging import redact_secrets
            raise OllamaResourceUnavailable('Ollama cannot load the model because memory is unavailable: '
                                            + redact_secrets(provider_error)[:500])
    response.raise_for_status()
    raw = response.json()
    if raw_response_path is not None:
        from atnos.core.logging import redact_secrets
        write_json(raw_response_path, {'response': redact_secrets(raw)})
    if think and isinstance(raw, dict) and raw.get('done_reason') == 'length':
        # A completed reasoning request consumed the whole configured context.
        # The same request cannot gain capacity by retrying. Keep the raw
        # evidence, use the existing resource-block path, and never silently
        # enlarge context, disable reasoning or accept a partial JSON verdict.
        capacity = (options or {}).get('num_ctx')
        input_tokens, output_tokens = raw.get('prompt_eval_count'), raw.get('eval_count')
        if (type(capacity) is int and capacity > 0
                and type(input_tokens) is int and input_tokens >= 0
                and type(output_tokens) is int and output_tokens >= 0
                and input_tokens + output_tokens >= capacity):
            raise OllamaResourceUnavailable(
                f'Ollama review context capacity exhausted: input={input_tokens}, '
                f'output={output_tokens}, num_ctx={capacity}. '
                'Stopped without retry; review context or execution configuration requires correction.')
    if not isinstance(raw, dict) or raw.get('done') is False or raw.get("done_reason") == "length":
        raise ValueError("Ollama returned an invalid or truncated completion")
    message = raw.get("message") if isinstance(raw, dict) else None
    content = message.get("content") if isinstance(message, dict) else raw.get("response", "")
    return parse_json(str(content or ""), reject_duplicate_keys=reject_duplicate_keys)


def parse_json(text: str, *, reject_duplicate_keys: bool = False) -> dict[str, Any] | list[Any] | None:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key in model response: ' + str(key)[:120])
            result[key] = value
        return result
    options = {'object_pairs_hook': unique_object} if reject_duplicate_keys else {}
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        parsed = json.loads(stripped, **options)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", stripped, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(1), **options)
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, (dict, list)) else None


def write_json(path: Path, payload: dict[str, Any]) -> StepResult:
    # Keep the last complete artifact until the replacement is fully written.
    # Same-directory staging stays on the target volume (and on E: in Atnos).
    # Match the production approval writers' flush/fsync/replace pattern.
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    target = path
    if os.name == "nt":
        absolute = os.path.abspath(path)
        # Long production evidence directories exceed legacy MAX_PATH even
        # when the artifact itself fits. Explicit extended paths also protect
        # the sibling staging file without changing the recorded artifact URL.
        if len(absolute) >= 240 and not absolute.startswith("\\\\?\\"):
            extended = "\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute
            target = Path(extended)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".atnos-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return StepResult(
        artifact_name=path.name,
        artifact_path=path,
        payload=payload,
        message=f"wrote {path.name}",
    )


def clean_words(value: str, limit: int) -> str:
    return " ".join(str(value or "").replace("`", "").split()[:limit]).strip()
