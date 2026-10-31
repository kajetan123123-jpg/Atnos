from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from atnos.agents.short_production.models import (
    ApprovedHook,
    NarrationPlan,
    PlanRequest,
    ScriptPlan,
    ShortAssetPlan,
    SubtitlesPlan,
    ThumbnailPlan,
    VisualSegment,
)
from atnos.core.config import Settings


class ShortProductionPlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_plan(
        self,
        request: PlanRequest,
        *,
        topic_title: str,
        output_dir: Path,
        style_guide: str | None = None,
    ) -> ShortAssetPlan:
        script = self._script(topic_title=topic_title, hook=request.approved_hook, language_hint=request.channel_id)
        visuals = self._visuals(topic_title=topic_title, script=script, style_guide=style_guide)
        thumbnail = self._thumbnail(topic_title=topic_title, hook=request.approved_hook)
        narration_path = output_dir / "assets" / "narration.mp3"
        return ShortAssetPlan(
            channel_id=request.channel_id,
            topic_id=request.topic_id,
            script=script,
            narration=NarrationPlan(
                voice_id=self.settings.elevenlabs_voice_id or "configure-elevenlabs-voice-id",
                speed=float(self.settings.elevenlabs_speed or 1.0),
                output_file=str(narration_path),
            ),
            visuals=visuals,
            thumbnail=thumbnail,
            subtitles=SubtitlesPlan(
                style=(style_guide or "bold mobile subtitles, high contrast, max 2 lines").splitlines()[0][:160],
                auto_generate=True,
            ),
            tool_sequence=self._tool_sequence(visuals),
            estimated_production_time_min=10 + len(visuals) * 4,
            status="planned",
        )

    def _script(self, *, topic_title: str, hook: ApprovedHook, language_hint: str) -> ScriptPlan:
        llm_script = self._llm_script(topic_title=topic_title, hook=hook)
        if llm_script is not None:
            return llm_script
        body = [
            f"Najpierw nazwij problem bardzo prosto: {topic_title} zwykle wyglada trudniej niz jest.",
            "W praktyce chodzi o jedna decyzje, ktora powtarza sie codziennie.",
            "Wiekszosc osob szuka skomplikowanej metody i przez to traci tempo.",
            "Lepsze podejscie to ustawic prosty schemat: zobacz, wybierz, wykonaj, sprawdz.",
            "Jesli wynik jest slaby, poprawiasz jeden element, a nie caly system.",
            "To dziala, bo zmniejsza chaos i daje szybka informacje zwrotna.",
            "Najwiekszy blad to czekanie na idealny moment zamiast maly test.",
        ]
        cta = "Zapisz ten schemat i uzyj go dzis przy jednej decyzji."
        return self._normalize_script(hook=hook, body=body, cta=cta)

    def _llm_script(self, *, topic_title: str, hook: ApprovedHook) -> ScriptPlan | None:
        prompt = (
            "Return JSON only. Generate a Polish YouTube Shorts script with keys body array and cta. "
            "Body must have 6-8 sentences, each max 20 words. Total script target 80-120 words. "
            f"Topic: {topic_title}. Hook: {hook.text}. Opening line: {hook.opening_line}."
        )
        for provider in (self._ask_ollama, self._ask_claude):
            try:
                payload = provider(prompt)
            except Exception:
                continue
            body = payload.get("body") if isinstance(payload, dict) else None
            cta = payload.get("cta") if isinstance(payload, dict) else None
            if isinstance(body, list) and isinstance(cta, str):
                try:
                    return self._normalize_script(
                        hook=hook,
                        body=[str(item) for item in body],
                        cta=cta,
                    )
                except ValueError:
                    continue
        return None

    def _normalize_script(self, *, hook: ApprovedHook, body: list[str], cta: str) -> ScriptPlan:
        cleaned_body = [_limit_words(_clean(line), 20) for line in body if _clean(line)]
        cleaned_body = cleaned_body[:8]
        while len(cleaned_body) < 6:
            cleaned_body.append("Dodaj jeden praktyczny przyklad i pokaz roznice w codziennym dzialaniu.")
        cleaned_cta = _limit_words(_clean(cta), 16)
        total_words = len(hook.text.split()) + len(hook.opening_line.split()) + len(cleaned_cta.split())
        total_words += sum(len(line.split()) for line in cleaned_body)
        if total_words < 80:
            cleaned_body.append("Najlepszy short nie obiecuje magii, tylko pokazuje prosty ruch do wykonania od razu.")
            total_words = len(hook.text.split()) + len(hook.opening_line.split()) + len(cleaned_cta.split())
            total_words += sum(len(line.split()) for line in cleaned_body)
        if total_words > 120:
            while total_words > 120 and len(cleaned_body) > 4:
                cleaned_body.pop()
                total_words = len(hook.text.split()) + len(hook.opening_line.split()) + len(cleaned_cta.split())
                total_words += sum(len(line.split()) for line in cleaned_body)
        return ScriptPlan(
            hook=hook.text,
            opening_line=hook.opening_line,
            body=cleaned_body,
            cta=cleaned_cta,
            total_words=total_words,
            estimated_duration_s=max(30, min(60, int(total_words / 2.25))),
        )

    def _visuals(self, *, topic_title: str, script: ScriptPlan, style_guide: str | None) -> list[VisualSegment]:
        segments: list[VisualSegment] = []
        grouped = [list(range(index, min(index + 2, len(script.body)))) for index in range(0, len(script.body), 2)]
        for index, lines in enumerate(grouped, start=1):
            segment_text = " ".join(script.body[item] for item in lines)
            tool = "runway" if index <= 2 else "stock"
            if "faceless" in (style_guide or "").casefold():
                tool = "facelessshorts"
            segments.append(
                VisualSegment(
                    segment_id=index,
                    script_lines=[item + 1 for item in lines],
                    tool=tool,  # type: ignore[arg-type]
                    prompt=(
                        f"Vertical 9:16 short scene about {topic_title}. "
                        f"Show the idea visually: {segment_text}. Clean, premium, readable, no clutter."
                    ),
                    duration_s=max(5, min(9, int(script.estimated_duration_s / max(1, len(grouped))))),
                    style_notes=style_guide or "consistent editorial faceless style, bold captions, warm contrast",
                )
            )
        return segments

    def _thumbnail(self, *, topic_title: str, hook: ApprovedHook) -> ThumbnailPlan:
        headline_words = _clean(hook.text).upper().split()[:3]
        headline = " ".join(headline_words) if headline_words else "WAZNY BLAD"
        return ThumbnailPlan(
            headline=headline,
            visual_concept=f"High-contrast faceless thumbnail for: {topic_title}. One clear object, one emotional contrast.",
            color_palette=["#101820", "#FEE715", "#F25F5C"],
        )

    def _tool_sequence(self, visuals: list[VisualSegment]) -> list[str]:
        visual_tools = []
        for visual in visuals:
            if visual.tool not in visual_tools:
                visual_tools.append(visual.tool)
        return ["elevenlabs", *visual_tools, "capcut"]

    def _ask_ollama(self, prompt: str) -> dict[str, Any]:
        model = self.settings.ollama_synthesis_model or self.settings.ollama_chat_model or "qwen3:14b"
        with httpx.Client(timeout=min(float(self.settings.ollama_timeout_s), 20.0)) as client:
            response = client.post(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
                    "think": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
            message = payload.get("message") if isinstance(payload, dict) else None
            if isinstance(message, dict):
                text = str(message.get("content") or "")
            else:
                text = str(payload.get("response") or "") if isinstance(payload, dict) else ""
        return json.loads(text)

    def _ask_claude(self, prompt: str) -> dict[str, Any]:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing.")
        model = os.getenv("ANTHROPIC_SYNTHESIS_MODEL") or os.getenv("ANTHROPIC_CHAT_MODEL") or "claude-sonnet-4-20250514"
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={"model": model, "max_tokens": 900, "messages": [{"role": "user", "content": prompt}]},
            )
            response.raise_for_status()
            content = response.json().get("content") or []
        text = str(content[0].get("text") or "") if isinstance(content, list) and content else ""
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        return json.loads(text)


def _clean(value: str) -> str:
    return " ".join(str(value or "").replace("`", "").split()).strip()


def _limit_words(value: str, max_words: int) -> str:
    return " ".join(value.split()[:max_words])
