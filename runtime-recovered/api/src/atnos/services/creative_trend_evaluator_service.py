from __future__ import annotations

import re
import unicodedata
from typing import Any


class CreativeTrendEvaluatorService:
    def evaluate(
        self,
        *,
        preview: dict[str, Any],
        trend_snapshot: dict[str, Any] | None,
    ) -> dict[str, object]:
        if not trend_snapshot:
            return {
                "score": 0.0,
                "matched_keywords": [],
                "matched_hashtags": [],
                "strengths": [],
                "risks": ["Brak swiezego sygnalu trendowego do porownania."],
                "recommended_reel_match": False,
            }

        script = dict(preview.get("script_bundle") or {})
        style = dict(preview.get("style") or {})
        reel = dict(preview.get("reel_type") or {})
        summary = dict(trend_snapshot.get("summary") or {})

        preview_text = " ".join(
            part
            for part in (
                str(preview.get("goal") or "").strip(),
                str(script.get("title") or "").strip(),
                str(script.get("hook") or "").strip(),
                " ".join(str(item).strip() for item in list(script.get("body") or []) if str(item).strip()),
                str(style.get("label") or "").strip(),
                str(reel.get("label") or "").strip(),
            )
            if part
        )
        preview_tokens = self._tokenize(preview_text)
        keywords = [self._normalize_token(item) for item in list(summary.get("top_keywords") or [])]
        hashtags = [
            self._normalize_token(str(item).lstrip("#"))
            for item in list(summary.get("top_hashtags") or [])
            if str(item).strip()
        ]
        matched_keywords = sorted({item for item in keywords if item and item in preview_tokens})
        matched_hashtags = sorted({item for item in hashtags if item and item in preview_tokens})
        recommended_reel_types = [
            str(item).strip().lower()
            for item in list(summary.get("recommended_reel_types") or [])
            if str(item).strip()
        ]
        reel_key = str(reel.get("key") or "").strip().lower()
        recommended_reel_match = bool(reel_key and reel_key in recommended_reel_types)

        score = min(
            12.0,
            float(len(matched_keywords)) * 2.5
            + float(len(matched_hashtags)) * 1.5
            + (2.0 if recommended_reel_match else 0.0),
        )
        strengths: list[str] = []
        risks: list[str] = []
        if matched_keywords:
            strengths.append(f"Material lapie aktualne slowa rynku: {', '.join(matched_keywords[:3])}.")
        if matched_hashtags:
            strengths.append(f"Jest zbieznosc z trendujacymi hashtagami: {', '.join(matched_hashtags[:3])}.")
        if recommended_reel_match:
            strengths.append("Wybrany format rolki pasuje do obecnego rytmu sygnalow trendowych.")
        if not matched_keywords and not matched_hashtags:
            risks.append("Brak wyraznego pokrycia z aktualnymi sygnalami klikow.")
        if not recommended_reel_match and recommended_reel_types:
            risks.append(
                f"Rynek teraz bardziej premiuje: {', '.join(recommended_reel_types[:3])}."
            )
        return {
            "score": round(score, 2),
            "matched_keywords": matched_keywords[:6],
            "matched_hashtags": matched_hashtags[:6],
            "strengths": strengths[:3],
            "risks": risks[:3],
            "recommended_reel_match": recommended_reel_match,
        }

    @classmethod
    def _tokenize(cls, value: str) -> set[str]:
        normalized = cls._normalize_token(value)
        return {
            token
            for token in re.split(r"[^a-z0-9]+", normalized)
            if len(token) >= 3 and token not in cls._stopwords()
        }

    @staticmethod
    def _normalize_token(value: str) -> str:
        lowered = unicodedata.normalize("NFKD", value.lower())
        return "".join(ch for ch in lowered if not unicodedata.combining(ch))

    @staticmethod
    def _stopwords() -> set[str]:
        return {
            "the",
            "and",
            "for",
            "that",
            "this",
            "you",
            "with",
            "from",
            "your",
            "jest",
            "czy",
            "sie",
            "dla",
            "jak",
            "ale",
            "nie",
            "tak",
            "pod",
            "too",
            "get",
            "are",
        }
