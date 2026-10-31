"""Generation schemas that cannot omit the resources required by a scene type."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel, create_model

from atnos.production.steps.order_assets import EditorialScene


class FootageScene(EditorialScene):
    kind: Literal["footage"]
    asset_id: str = Field(min_length=1, max_length=100)
    source_start: float = Field(ge=0, le=300)
    labels: list[str] = Field(max_length=0)
    claim_ids: list[str] = Field(max_length=2)
    transition: Literal["cut", "match_cut"]


class ActionScene(EditorialScene):
    kind: Literal["steps", "flow"]
    asset_id: Literal[None]
    source_start: Literal[0]
    labels: list[str] = Field(min_length=1, max_length=3)
    claim_ids: list[str] = Field(max_length=2)
    transition: Literal["cut", "match_cut"]


class NumberScene(EditorialScene):
    kind: Literal["number", "chart"]
    asset_id: Literal[None]
    source_start: Literal[0]
    labels: list[str] = Field(max_length=0)
    claim_ids: list[str] = Field(min_length=1, max_length=1)
    transition: Literal["cut", "match_cut"]


class ComparisonScene(EditorialScene):
    kind: Literal["comparison"]
    asset_id: Literal[None]
    source_start: Literal[0]
    labels: list[str] = Field(min_length=2, max_length=2)
    claim_ids: list[str] = Field(min_length=2, max_length=2)
    transition: Literal["cut", "match_cut"]


RenderableScene = Annotated[FootageScene | ActionScene | NumberScene | ComparisonScene, Field(discriminator="kind")]


class SceneResponse(RootModel[RenderableScene]):
    def editorial(self):
        return EditorialScene.model_validate(self.model_dump(mode="json"))


class ScenePlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenes: list[RenderableScene] = Field(min_length=4, max_length=14)


def scene_type(script, segment, library, *, required_seconds=0):
    """Constrain decoding to resources this particular narration can actually use."""
    from atnos.production.chart_series import verified_chart_series
    from atnos.production.request_fidelity import requires_action_diagram

    claims = {claim.id: claim for claim in script.claims}
    claim_ids = tuple(key for key in segment.claim_ids if key in claims)
    asset_ids = tuple(entry['id'] for entry in library['assets']
                      if entry['duration_seconds'] >= max(.5, required_seconds + .04))
    claim_type = list[Literal[claim_ids]] if claim_ids else list[str]
    fields = {'segment_id': (Literal[segment.id], ...),
              'claim_ids': (claim_type, Field(max_length=2 if claim_ids else 0))}
    variants = [create_model(f'Actions_{segment.id}', __base__=ActionScene, **fields)]
    if not requires_action_diagram(segment):
        if asset_ids:
            variants.append(create_model(f'Footage_{segment.id}', __base__=FootageScene,
                                          asset_id=(Literal[asset_ids], ...), **fields))
        if claim_ids:
            numeric_fields = {**fields, 'claim_ids': (claim_type, Field(min_length=1, max_length=1))}
            variants.append(create_model(f'Number_{segment.id}', __base__=NumberScene,
                                          kind=(Literal['number'], ...), **numeric_fields))
            chart_ids = []
            for key in claim_ids:
                try:
                    verified_chart_series(claims[key].model_dump(), segment.text)
                except ValueError:
                    continue
                chart_ids.append(key)
            if chart_ids:
                variants.append(create_model(f'Chart_{segment.id}', __base__=NumberScene,
                    kind=(Literal['chart'], ...), segment_id=(Literal[segment.id], ...),
                    claim_ids=(list[Literal[tuple(chart_ids)]], Field(min_length=1, max_length=1))))
            if len(claim_ids) >= 2:
                variants.append(create_model(f'Comparison_{segment.id}', __base__=ComparisonScene,
                    **{**fields, 'claim_ids': (claim_type, Field(min_length=2, max_length=2))}))
    return variants[0] if len(variants) == 1 else Annotated[Union[tuple(variants)], Field(discriminator='kind')]


def scene_response_for(script, segment, library, *, required_seconds=0):
    return create_model('SceneResponse', __base__=RootModel[
        scene_type(script, segment, library, required_seconds=required_seconds)])


def plan_response_for(script, library):
    # Named slots avoid an unrestricted list where a chart from another segment is legal.
    slots = create_model('ScriptSceneSlots', __config__=ConfigDict(extra='forbid'), **{
        f'scene_{index}': (scene_type(script, segment, library), Field(alias=segment.id))
        for index, segment in enumerate(script.segments)
    })
    return create_model('ScenePlanResponse', __config__=ConfigDict(extra='forbid'), scenes=(slots, ...))


def scene_decoding_schema(model):
    """Preserve the small renderer label bounds in the actual planner decoder."""
    from atnos.production.steps.order_script import generation_schema
    schema=generation_schema(model)
    original=model.model_json_schema()
    for name,definition in schema.get('$defs',{}).items():
        labels=definition.get('properties',{}).get('labels')
        source=original.get('$defs',{}).get(name,{}).get('properties',{}).get('labels',{})
        if not isinstance(labels,dict):
            continue
        for key in ('minItems','maxItems'):
            if key in source:
                labels[key]=source[key]
        labels['items']={'type':'string','minLength':1,'maxLength':42}
    return schema


def planning_constraints(script, library):
    """A short readable companion to the enforced per-segment response schema."""
    rows = []
    for segment in script.segments:
        schema = scene_response_for(script, segment, library).model_json_schema()
        kinds = []
        for definition in schema.get('$defs', {}).values():
            field = definition.get('properties', {}).get('kind', {})
            kinds.extend(field.get('enum', [field['const']] if 'const' in field else []))
        rows.append({'segment_id': segment.id, 'allowed_kinds': sorted(set(kinds)),
                     'allowed_claim_ids': segment.claim_ids})
    return rows


RENDER_CAPABILITIES = (
    "The renderer can display only: footage (the actual unmodified source video), steps (1-3 text cards), "
    "flow (1-3 text cards linked with arrows), number (one computed value and assumptions), "
    "chart (one verified time-series), comparison (two verified values and labels). "
    "No invented app screens, icons, split screens or overlays on footage. "
    "semantic_goal must describe the renderable output selected HERE, not blindly copy a writer's aspirational visual_intent. "
    "Preserve the spoken meaning when choosing another visual representation. Match footage to observed content; "
    "if it cannot show the idea, choose an explicit native diagram, not footage with a missing asset_id. "
    "Always return every schema field, including empty arrays, null asset_id and source_start=0 for native graphics. "
)
