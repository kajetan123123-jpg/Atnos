"""Lossless supplied-script intake and executable adaptation contract checks."""
from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


INTAKE_HOLD = "supplied_script_contract_pending"


class ScriptIntake(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_attachment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    factcheck_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario_index: int = Field(ge=1, le=4)
    source_language: Literal["pl"] = "pl"
    requested_language: Literal["en", "pl"]
    target_duration_seconds: int = Field(ge=15, le=85)
    original_scenario: str = Field(min_length=30, max_length=16000)
    corrected_brief: str = Field(min_length=30, max_length=12000)
    fact_source_urls: list[HttpUrl] = Field(default_factory=list, max_length=15)
    fact_review: Literal["parent_supplied_not_production_qa"] = "parent_supplied_not_production_qa"
    delivery_mode: Literal["local_only"] = "local_only"


def intake_blockers(intake: dict) -> list[str]:
    from atnos.production.hypothetical_script_intake import HypotheticalScriptIntake, is_hypothetical
    if is_hypothetical(intake):
        try:
            HypotheticalScriptIntake.model_validate(intake)
        except ValueError as exc:
            return ["The hypothetical supplied-script intake is incomplete or invalid: " + str(exc)]
        return []
    try:
        ScriptIntake.model_validate(intake)
    except ValueError:
        return ["The supplied-script intake is incomplete or invalid"]
    blockers = []
    if intake["requested_language"] != "en":
        blockers.append("Polish TTS, ASR and numeric speech validation are not supported by this order pipeline.")
    try:
        brief = json.loads(intake["corrected_brief"])
        if brief.get("language") != intake["requested_language"] or not brief.get("calculations"):
            raise ValueError("The reviewed brief must include matching language and calculations")
        facts = brief["verified_external_facts"]
        if not isinstance(facts, list) or not facts:
            raise ValueError("Reviewed source facts are missing")
        ids = set()
        for fact in facts:
            source = urlsplit(fact["source"])
            if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", fact["id"]) or fact["id"] in ids
                    or source.scheme != "https" or source.netloc != "www.gov.pl"
                    or fact["source"] not in [str(url) for url in intake["fact_source_urls"]]
                    or len(fact["statement"]) < 10):
                raise ValueError("Invalid or unbound reviewed source fact")
            ids.add(fact["id"])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        blockers.append("Supplied-script brief is not executable: " + str(exc))
    return blockers


def intake_error(intake: dict) -> dict:
    return {"category": INTAKE_HOLD, "failures": intake_blockers(intake)}
