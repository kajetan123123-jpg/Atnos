from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from atnos.core.config import Settings
from atnos.production.order_intake import ScriptIntake
from atnos.production.hypothetical_script_intake import HypotheticalScriptIntake


def content_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


class ProductionOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project: Literal["wealth_blueprint", "children_stories", "horror_stories"] = "wealth_blueprint"
    production_scope: Literal['film', 'script_preview'] = 'film'
    topic: str = Field(min_length=3, max_length=300)
    direction: str = Field(default="", max_length=2000)
    optional_notes: str = Field(default="", max_length=4000)
    delivery_mode: Literal["project_policy", "local_only"] = "project_policy"
    series_key: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    intake: ScriptIntake | HypotheticalScriptIntake | None = None
    brand_kit: str | None = Field(default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,79}$')
    visual_style_id: Literal["A", "B", "C", "D"] | None = None
    visual_presentation: Literal['educational_illustration'] | None = None
    native_illustration_system: Literal['wb_planning_desk_painted_v1'] | None = None
    production_set_id: Literal['wb-editorial-v1'] | None = None
    voice_candidate: Literal["Roger-v3", "Ryan-B", "Ryan-neutral"] | None = None
    provider_policy: Literal['project_default', 'strict_local'] = 'project_default'
    target_duration_seconds: int | None = Field(default=None, ge=18, le=88)
    image_provider: Literal["comfyui-local"] | None = None
    image_reference_id: Literal["wb-original-board"] | None = None

    def validate_automatic_submission(self) -> None:
        """Guard new immediate dispatch only; preserve existing job-scoped lanes."""
        import re
        import unicodedata
        text = unicodedata.normalize('NFKD', (self.direction + ' ' + self.optional_notes).casefold().replace('ł', 'l')).encode('ascii', 'ignore').decode()
        if self.project == 'wealth_blueprint' and (
            re.search(r'\b(?:glos\w*|voice)\s+ryan\b|\bryan\s+b\b', text)
            or re.search(r'\bbez\s+platnych\s+(?:api|uslug)\b|\bno\s+paid\s+(?:apis?|services?|tools?)\b|\bbezplatnych\s+narzedzi\b|\bbezplatnie\b|\b(?:wylacznie|tylko)\s+darmowe\s+narzedzia\b|\bfree\s+tools\s+only\b|\bbez\s+kosztow\s+api\b|\b(?:budzet|budget)\s*[:=]?\s*(?:usd\s*|\$\s*)?0(?:[.,]0+)?(?!\d|[.,]\d)', text)
        ):
            if self.voice_candidate not in {'Ryan-B', 'Ryan-neutral'} or self.provider_policy != 'strict_local':
                raise ValueError('Nie uruchomiono produkcji: wybierz lokalny głos Ryan B i tryb wyłącznie lokalny. Nie zastąpię go Piperem ani płatnym API.')

    @model_serializer(mode="wrap")
    def omit_unselected_image_options(self, handler):
        payload = handler(self)
        if self.provider_policy == 'project_default':
            payload.pop('provider_policy', None)
        if self.target_duration_seconds is None:
            payload.pop('target_duration_seconds', None)
        if payload.get('visual_presentation') is None:
            payload.pop('visual_presentation', None)
        if payload.get('native_illustration_system') is None:
            payload.pop('native_illustration_system', None)
        if self.production_set_id is None:payload.pop('production_set_id',None)
        if payload.get("image_provider") is None:
            payload.pop("image_provider", None)
        if payload.get("image_reference_id") is None:
            payload.pop("image_reference_id", None)
        return payload

    @model_validator(mode='after')
    def supported_scope(self):
        if self.production_set_id and (self.project!='wealth_blueprint' or self.delivery_mode!='local_only'
                or self.provider_policy!='strict_local' or self.voice_candidate not in {'Ryan-B','Ryan-neutral'}
                or self.brand_kit or self.image_provider or self.image_reference_id or self.visual_style_id or self.visual_presentation):
            raise ValueError('Zestaw WB wymaga lokalnego filmu z jawnym Ryanem, bez drugiego stylu lub generowania obrazów.')
        if self.production_set_id:
            from atnos.production.wb_production_set import SYSTEM_ID
            # Persist the derived selection in the normal request too: the
            # adapter deliberately compares request and frozen profile.
            if self.native_illustration_system not in {None,SYSTEM_ID}:
                raise ValueError('Wybrany system ilustracji nie pasuje do zestawu WB.')
            self.native_illustration_system=SYSTEM_ID
        if (self.voice_candidate in {'Ryan-B', 'Ryan-neutral'} or self.provider_policy == 'strict_local') and (
                self.project != 'wealth_blueprint' or self.delivery_mode != 'local_only'
                or self.voice_candidate not in {'Ryan-B', 'Ryan-neutral'} or self.provider_policy != 'strict_local'):
            raise ValueError('Ryan B wymaga jawnego lokalnego zlecenia WB bez płatnych dostawców.')
        if self.target_duration_seconds is not None and (self.project != 'wealth_blueprint' or self.intake is not None):
            raise ValueError('Wybierz jeden sposób określenia czasu: pole długości albo istniejący brief intake WB.')
        if self.native_illustration_system and (self.project != 'wealth_blueprint' or self.delivery_mode != 'local_only' or self.brand_kit or self.image_provider):
            raise ValueError('Natywna ilustracja wymaga osobnej lokalnej próby WB bez innego zestawu assetów.')
        if self.visual_presentation and (self.project != 'wealth_blueprint' or self.delivery_mode != 'local_only' or self.brand_kit or self.visual_style_id):
            raise ValueError('Edukacyjna kompozycja wymaga lokalnej próby WB bez innego wariantu marki.')
        if isinstance(self.intake, HypotheticalScriptIntake) and (self.project != 'wealth_blueprint' or self.delivery_mode != 'local_only'):
            raise ValueError('Hipotetyczny scenariusz wymaga lokalnego zlecenia Wealth Blueprint.')
        if self.voice_candidate and (self.project != 'wealth_blueprint' or self.delivery_mode != 'local_only'):
            raise ValueError('Kandydat głosu wymaga lokalnej próby Wealth Blueprint.')
        if self.brand_kit and (self.project != 'wealth_blueprint' or self.delivery_mode != 'local_only' or self.intake):
            raise ValueError('Próba identyfikacji marki wymaga lokalnego zlecenia Wealth Blueprint bez drugiego briefu intake.')
        if self.visual_style_id and (self.project != 'wealth_blueprint' or self.brand_kit):
            raise ValueError('Wariant stylu A/B/C/D jest dostępny tylko dla Wealth Blueprint bez osobnego brand kitu.')
        image_selection = (self.image_provider, self.image_reference_id, self.visual_style_id)
        if self.image_provider is not None or self.image_reference_id is not None:
            if not all(value is not None for value in image_selection):
                raise ValueError('Lokalne obrazy wymagają jawnego wyboru dostawcy, referencji i stylu A/B/C/D.')
            if (self.project != 'wealth_blueprint' or self.delivery_mode != 'local_only'
                    or self.production_scope != 'film' or self.brand_kit):
                raise ValueError('Lokalny ComfyUI jest dostępny tylko dla lokalnego filmu Wealth Blueprint bez osobnego brand kitu.')
        if self.project != 'wealth_blueprint':
            if self.production_scope != 'script_preview' or self.intake is not None or self.delivery_mode != 'local_only':
                raise ValueError('Dla opowieści dostępny jest lokalny podgląd scenariusza. Produkcja obrazu i głosu wymaga jeszcze podłączenia.')
        elif self.production_scope != 'film':
            raise ValueError('Podgląd opowieści jest dostępny dla kanału bajkowego i strasznych historii.')
        return self

    @field_validator("project", mode="before")
    @classmethod
    def normalize_project(cls, value):
        return str(value).strip().casefold().replace(" ", "_")


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    provider: Literal["elevenlabs", "qwen_local"]
    voice_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    model: str = Field(min_length=1, max_length=80)
    stability: float = Field(ge=0, le=1)
    similarity: float = Field(ge=0, le=1)
    speed: float = Field(ge=.7, le=1.2)
    pacing_mode: Literal['fixed', 'script_length'] = 'fixed'
    style: float = Field(ge=0, le=1)
    speaker_boost: bool
    reservation_usd_per_1000_characters: float = Field(default=.4, gt=0, le=5)


class ScriptModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generator: str = Field(min_length=1, max_length=100)
    reviewer: str = Field(min_length=1, max_length=100)
    context_tokens: int = Field(ge=4096, le=32768)
    output_tokens: int = Field(ge=1024, le=8192)
    timeout_seconds: int = Field(ge=30, le=900)
    thinking: bool = False


class AssetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifests: list[str] = Field(min_length=1, max_length=12)
    embedding_model: str = Field(min_length=1, max_length=100)
    candidates_per_scene: int = Field(ge=2, le=12)
    max_assets: int = Field(ge=5, le=100)


class VisualQAProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model: str = Field(default="qwen3-vl:8b-instruct", min_length=1, max_length=100)
    timeout_seconds: int = Field(default=240, ge=30, le=900)
    minimum_score: float = Field(default=7, ge=5, le=10)


class ProductionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: int = Field(ge=1)
    renderer: Literal["editorial", "remotion"]
    renderer_remotion_opt_in: bool = True
    renderer_default_switch_requires: str = Field(
        default="two_consecutive_green_remotion_order_e2e",
        min_length=1,
        max_length=120,
    )
    renderer_notes: str = Field(default="", max_length=500)
    language: Literal["en"]
    example_currency: Literal["dollars", "PLN"] = "dollars"
    duration_seconds: tuple[int, int]
    word_budget: tuple[int, int]
    minimum_practical_share: float = Field(ge=1 / 3, le=1)
    max_attempts_per_element: int = Field(ge=1, le=100)
    max_provider_calls: int = Field(ge=1, le=100000)
    max_cost_usd: float = Field(ge=0, le=100000)
    voice: VoiceProfile
    script_model: ScriptModelProfile
    assets: AssetProfile
    visual_qa: VisualQAProfile = Field(default_factory=VisualQAProfile)
    narrative_rules: list[str] = Field(min_length=1, max_length=40)
    visual_rules: list[str] = Field(min_length=1, max_length=40)
    reference_profile: str = Field(min_length=1)
    knowledge_queries: list[str] = Field(min_length=1, max_length=20)
    caption_max_words: int = Field(ge=2, le=8)
    caption_max_characters: int = Field(ge=12, le=55)
    safe_area: tuple[int, int, int, int]
    width: Literal[1080]
    height: Literal[1920]
    fps: Literal[30]
    thumbnail_rules: list[str] = Field(min_length=1, max_length=30)
    required_gates: list[str] = Field(min_length=1)
    publish: Literal[False] = False

    @model_validator(mode="after")
    def validate_ranges(self):
        if not 15 <= self.duration_seconds[0] < self.duration_seconds[1] <= 90:
            raise ValueError("Invalid Short duration range")
        if not 40 <= self.word_budget[0] < self.word_budget[1] <= 240:
            raise ValueError("Invalid narration word range")
        left, top, right, bottom = self.safe_area
        if not (0 <= left < right <= self.width and 0 <= top < bottom <= self.height):
            raise ValueError("Invalid caption safe area")
        minimum_gates = {"script", "voice", "captions", "media", "semantic", "package"}
        if not minimum_gates.issubset(self.required_gates):
            raise ValueError("A critical production gate is missing")
        return self


def profile_for_order(profile: dict, order: ProductionOrder) -> dict:
    """Change only this order's frozen timing, never the saved project or budgets."""
    if (order.intake is None or order.intake.requested_language != "en") and not order.visual_style_id and not order.voice_candidate and not order.visual_presentation and not order.native_illustration_system and order.target_duration_seconds is None:
        return profile
    import copy

    scoped = copy.deepcopy(profile)
    if order.native_illustration_system:
        scoped['native_illustration_system'] = order.native_illustration_system
    if order.production_set_id:
        from atnos.production.wb_production_set import freeze_production_set
        scoped['production_set']=freeze_production_set(order.voice_candidate)
        scoped['native_illustration_system']=scoped['production_set']['native_illustration_system']
    if order.visual_presentation:
        scoped['visual_presentation'] = order.visual_presentation
    if order.voice_candidate in {'Ryan-B', 'Ryan-neutral'}:
        from atnos.production.local_voice_lane import MODEL_ID, intake_voice_preset
        voice = dict(scoped['production']['voice'])
        voice.update(provider='qwen_local', voice_id='Ryan', model=MODEL_ID, speed=1., pacing_mode='fixed')
        scoped['production']['voice'] = VoiceProfile.model_validate(voice).model_dump(mode='json')
        scoped['voice_candidate'] = {'id': order.voice_candidate, 'scope': 'local_trial', 'preset': intake_voice_preset(order.voice_candidate),
                                   'user_approved_as_channel_default': False}
        scoped['provider_policy'] = 'strict_local'
        scoped['production']['max_cost_usd'] = 0
    elif order.voice_candidate:
        voice = dict(scoped['production']['voice'])
        voice.update(voice_id='CwhRBWXzGAHq8TQ4Fs17', model='eleven_v3', stability=.5,
                     similarity=.75, speed=1., pacing_mode='fixed', style=0., speaker_boost=False)
        scoped['production']['voice'] = VoiceProfile.model_validate(voice).model_dump(mode='json')
        scoped['voice_candidate'] = {'id': order.voice_candidate, 'scope': 'local_trial',
                                     'user_approved_as_channel_default': False}
    if order.visual_style_id:
        from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style

        scoped["visual_style"] = freeze_visual_style(order.visual_style_id)
    if order.target_duration_seconds is None and (order.intake is None or order.intake.requested_language != "en"):
        return scoped
    target = order.target_duration_seconds or order.intake.target_duration_seconds
    production = scoped["production"]
    if isinstance(order.intake, HypotheticalScriptIntake):
        production["example_currency"] = order.intake.example_currency
    production["duration_seconds"] = [max(15, target - 2), target + 2]
    production["word_budget"] = [max(40, int(target * 2.2)), max(41, int(target * 2.7))]
    production = ProductionProfile.model_validate(production).model_dump(mode="json")
    scoped["production"] = production
    scoped["order_timing"] = {"target_seconds": target, "tolerance_seconds": 2,
                             "measurement_status": "not_measured"}
    return scoped


def load_project_profile(settings: Settings, project: str) -> dict:
    # A closed project key prevents caller-controlled file paths and profile escapes.
    key = str(project).strip().casefold().replace(' ', '_')
    if key not in {'wealth_blueprint', 'children_stories', 'horror_stories'}:
        raise ValueError('Unsupported production project')
    override = settings.data_dir / "production-profiles" / f"{key}.json"
    source = override if override.is_file() else Path(__file__).resolve().parents[3] / "config" / "channel_profiles" / f"{key}.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    if key != 'wealth_blueprint':
        from atnos.production.fiction_profile import FictionProductionProfile
        production = FictionProductionProfile.model_validate(raw['production'])
        expected_kind = 'children_story' if key == 'children_stories' else 'fiction_horror'
        if production.content_kind != expected_kind or raw['language'] != 'en':
            raise ValueError('The story profile does not match its project or English language')
        return {'project': key, 'account_id': raw['account_id'], 'channel_name': raw['channel_name'],
                'language': 'en', 'production': production.model_dump(mode='json')}
    production = ProductionProfile.model_validate(raw["production"])
    reference = (settings.data_dir / production.reference_profile).resolve()
    if not reference.is_relative_to(settings.data_dir.resolve()):
        raise ValueError("Reference profile must remain inside Atnos data")
    for manifest in production.assets.manifests:
        if not (settings.data_dir / manifest).resolve().is_relative_to(settings.data_dir.resolve()):
            raise ValueError("Asset manifest must remain inside Atnos data")
    return {"project": key, "account_id": raw["account_id"], "channel_name": raw["channel_name"],
            "language": raw["language"], "allowed_topics": raw["allowed_topics"],
            "forbidden_topics": raw["forbidden_topics"], "production": production.model_dump(mode="json")}
