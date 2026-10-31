"""Repeatable live smoke for Atnos' local Ollama roles (read-only tools only)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

API_ROOT = Path(__file__).resolve().parents[1] / "runtime-recovered" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.core.config import Settings  # noqa: E402
from atnos.llm.ollama import OllamaChatBackend  # noqa: E402
from atnos.llm.role_registry import ModelCapability, ModelRole, OllamaRoleRegistry  # noqa: E402


async def probe(args: argparse.Namespace) -> dict:
    settings = Settings(
        data_dir=API_ROOT / "data",
        ollama_base_url=args.url,
        ollama_fast_model=args.chat,
        ollama_main_model=args.script,
        ollama_vision_model=args.vision,
        semantic_knowledge_embedding_model=args.embedding,
    )
    results: dict[str, dict] = {}
    installed: set[str] = set()

    async def record(name: str, operation):
        started = time.monotonic()
        try:
            result = await operation()
            result.setdefault("ok", True)
        except Exception as exc:  # A probe must report all roles, even after one failure.
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:350]}
        result["elapsed_s"] = round(time.monotonic() - started, 2)
        results[name] = result

    async with httpx.AsyncClient(timeout=150.0) as client:
        async def inventory():
            version_response = await client.get(f"{args.url}/api/version")
            version_response.raise_for_status()
            tags_response = await client.get(f"{args.url}/api/tags")
            tags_response.raise_for_status()
            names = {str(item.get("name")) for item in tags_response.json().get("models", [])}
            installed.update(names)
            required = {args.chat, args.script, args.vision, args.embedding}
            return {
                "ok": required.issubset(names),
                "server_version": version_response.json().get("version"),
                "installed_models": sorted(names),
                "missing_roles": sorted(required - names),
            }

        await record("inventory", inventory)

        async def chat():
            backend = OllamaChatBackend(settings=settings, model=args.chat, think=False)
            answer = await backend.chat_complete(messages=[{
                "role": "user",
                "content": "Odpowiedz po polsku jednym zdaniem: co robi Atnos? Koordynuje tworzenie filmów YouTube.",
            }])
            return {
                "ok": bool(answer.content) and not answer.tool_calls,
                "requested_model": args.chat,
                "runtime_model": (answer.raw_payload or {}).get("model"),
                "answer": answer.content[:350],
            }

        await record("chat.quick", chat)

        async def tools():
            backend = OllamaChatBackend(settings=settings, model=args.chat, think=False)
            answer = await backend.chat_complete(
                messages=[{"role": "user", "content": "Sprawdź aktywny kanał za pomocą get_active_channel. Nie zgaduj."}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "get_active_channel",
                        "description": "Read-only test: returns the active channel",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }],
            )
            names = [call.name for call in answer.tool_calls]
            return {
                "ok": "get_active_channel" in names,
                "requested_model": args.chat,
                "runtime_model": (answer.raw_payload or {}).get("model"),
                "tool_names": names,
                "executed_pc_action": False,
            }

        await record("agent.tool_call", tools)

        async def structured():
            schema = {
                "type": "object",
                "properties": {"action": {"type": "string"}, "duration_seconds": {"type": "number"}},
                "required": ["action", "duration_seconds"],
                "additionalProperties": False,
            }
            response = await client.post(f"{args.url}/api/chat", json={
                "model": args.script,
                "messages": [{"role": "user", "content": "Return JSON: action='show the savings choice', duration_seconds=5."}],
                "format": schema,
                "think": False,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 90},
            })
            response.raise_for_status()
            body = response.json()
            parsed = json.loads(body["message"]["content"])
            return {
                "ok": isinstance(parsed.get("action"), str) and parsed.get("duration_seconds") == 5,
                "requested_model": args.script,
                "runtime_model": body.get("model"),
                "result": parsed,
            }

        await record("production.script_json", structured)

        async def vision():
            if not args.image or not args.image.is_file():
                return {"ok": False, "error": "An E: test image is required for vision"}
            encoded = base64.b64encode(args.image.read_bytes()).decode("ascii")
            response = await client.post(f"{args.url}/api/chat", json={
                "model": args.vision,
                "messages": [{
                    "role": "user",
                    "content": "Read the exact text of the WEEK 1, WEEK 2, WEEK 3 boxes. What object is in the center?",
                    "images": [encoded],
                }],
                "think": False,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 140},
            })
            response.raise_for_status()
            body = response.json()
            answer = str(body.get("message", {}).get("content") or "")
            return {
                "ok": all(term in answer.upper() for term in ("WEEK 1", "WEEK 2", "WEEK 3", "SET ASIDE")),
                "requested_model": args.vision,
                "runtime_model": body.get("model"),
                "image": str(args.image),
                "answer": answer[:500],
            }

        await record("screen.vision", vision)

        async def embedding():
            response = await client.post(f"{args.url}/api/embed", json={
                "model": args.embedding,
                "input": ["save part of a raise", "weekly savings habit"],
            })
            response.raise_for_status()
            vectors = response.json().get("embeddings") or []
            return {
                "ok": len(vectors) == 2 and len(vectors[0]) > 100 and len(vectors[0]) == len(vectors[1]),
                "requested_model": args.embedding,
                "vector_count": len(vectors),
                "dimensions": len(vectors[0]) if vectors else 0,
            }

        await record("knowledge.embedding", embedding)

    verified = {}
    if results["chat.quick"]["ok"] and results["agent.tool_call"]["ok"]:
        verified[args.chat] = {ModelCapability.TEXT, ModelCapability.TOOLS}
    if results["production.script_json"]["ok"]:
        verified.setdefault(args.script, set()).update({ModelCapability.TEXT, ModelCapability.STRUCTURED_JSON})
    if results["screen.vision"]["ok"]:
        verified.setdefault(args.vision, set()).update({ModelCapability.TEXT, ModelCapability.VISION})
    if results["knowledge.embedding"]["ok"]:
        verified.setdefault(args.embedding, set()).add(ModelCapability.EMBEDDING)
    registry = OllamaRoleRegistry(settings)
    for role, frozen in (
        (ModelRole.CHAT_QUICK, None),
        (ModelRole.AGENT_QUICK, None),
        (ModelRole.SCREEN_VISION, None),
        (ModelRole.PRODUCTION_SCRIPT_GENERATOR, args.script),
        (ModelRole.KNOWLEDGE_EMBEDDING, None),
    ):
        try:
            binding = registry.resolve(
                role,
                frozen_model=frozen,
                installed_models=installed,
                verified_capabilities=verified,
            )
            results[f"registry.{role.value}"] = {"ok": True, "model": binding.model, "evidence": binding.evidence}
        except Exception as exc:
            results[f"registry.{role.value}"] = {"ok": False, "error": str(exc)}

    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "url": args.url,
        "overall": "pass" if all(item["ok"] for item in results.values()) else "fail",
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--chat", default="qwen3:8b")
    parser.add_argument("--script", default="qwen3:14b")
    parser.add_argument("--vision", default="qwen3-vl:8b-instruct")
    parser.add_argument("--embedding", default="bge-m3:latest")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"overall": result["overall"], "checks": {k: v["ok"] for k, v in result["results"].items()}}))
    return 0 if result["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
