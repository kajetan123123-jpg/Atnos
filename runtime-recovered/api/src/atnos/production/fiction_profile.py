"""Story-only profiles; financial production rules do not apply to fiction."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atnos.production.project_profile import ScriptModelProfile


class FictionProductionProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    version: Literal[1] = 1
    content_kind: Literal['children_story', 'fiction_horror']
    language: Literal['en'] = 'en'
    audience: str = Field(min_length=5, max_length=200)
    duration_seconds: tuple[int, int] = (35, 50)
    word_budget: tuple[int, int] = (90, 115)
    max_attempts_per_element: int = Field(default=20, ge=1, le=100)
    max_provider_calls: int = Field(default=10000, ge=1, le=100000)
    max_cost_usd: float = Field(default=1000, gt=0, le=100000)
    script_model: ScriptModelProfile
    narrative_rules: list[str] = Field(min_length=3, max_length=30)
    visual_rules: list[str] = Field(min_length=3, max_length=30)
    publish: Literal[False] = False

    @model_validator(mode='after')
    def ranges(self):
        if not 40 <= self.word_budget[0] < self.word_budget[1] <= 160:
            raise ValueError('Invalid story word range')
        if not 15 <= self.duration_seconds[0] < self.duration_seconds[1] <= 90:
            raise ValueError('Invalid story duration range')
        return self
