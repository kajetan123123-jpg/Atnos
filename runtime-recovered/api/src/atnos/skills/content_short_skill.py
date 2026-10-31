from __future__ import annotations

import re
import unicodedata


def detect_local_polish_short_request(text: str) -> bool:
    normalized = _normalize(text)
    return (
        any(token in normalized for token in ("zrob", "stworz", "wygeneruj", "przygotuj"))
        and any(token in normalized for token in ("short", "shorta", "shorts", "reel", "rolka"))
        and "po polsku" in normalized
    )


def extract_short_topic(text: str) -> str:
    working = _normalize(text)
    working = re.sub(r"^(atnos[:, ]*)?", "", working).strip()
    for prefix in ("zrob mi", "zrob", "stworz mi", "stworz", "wygeneruj mi", "wygeneruj", "przygotuj mi", "przygotuj"):
        if working.startswith(prefix):
            working = working[len(prefix):].strip()
            break
    working = re.sub(r"\b(shorta|short|shorts|reel|rolke|rolka|po polsku|o)\b", " ", working)
    return " ".join(working.split()).strip()


def format_short_agent_response(result) -> str:
    quality_run = result.quality_run or {}
    quality_line = ""
    if quality_run:
        quality_line = (
            f"\n- quality: `{quality_run.get('status')}` "
            f"score `{quality_run.get('score')}` "
            f"report `{quality_run.get('report_path')}`"
        )
    if result.status == "NEEDS_HUMAN_REVIEW":
        return (
            "Short nie przeszedł quality gate po repair passach.\n"
            f"- output_dir: `{result.output_dir}`\n"
            f"- score: `{result.quality_report.get('overall_score')}`\n"
            "- status: `NEEDS_HUMAN_REVIEW`"
            f"{quality_line}"
        )
    return (
        "Lokalny short jest gotowy do ręcznego uploadu.\n"
        f"- final.mp4: `{result.final_mp4}`\n"
        f"- output_dir: `{result.output_dir}`\n"
        f"- QA: `{result.artifacts.get('render_qa')}`\n"
        f"- production report: `{result.artifacts.get('production_report')}`"
        f"{quality_line}"
    )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.casefold().split())
