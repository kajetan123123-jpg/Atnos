"""Studio admission to the existing registered renderer correction, not another queue."""
from pydantic import BaseModel,ConfigDict,Field
from atnos.production.order_store import ProductionBlocked


class StudioRendererRepair(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    request_key:str=Field(pattern=r'^[a-zA-Z0-9_-]{8,100}$')
    expected_package_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    expected_renderer_source:str=Field(pattern=r'^[0-9a-f]{64}$')
    note:str=Field(min_length=20,max_length=2000)


def presentation_snapshot(store,job_id):
    from atnos.production.publication_coordinator import verified_package
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import renderer_source_fingerprint
    from atnos.production.project_manifest import load_project_manifest
    from atnos.production.scoped_script_revision import verified_current_renderer_review
    state=store.get(job_id)
    result={'can_repair':False,'reason':'','scope':'renderer_and_downstream_only',
        'keeps':['script','illustrations','voice','captions','counters','history'],
        'user_appearance_approved':False}
    try:
        if state['worker_live'] or state['status']!='ready':
            raise ProductionBlocked('Poprawka montażu wymaga gotowej rolki i bezczynnego workera. Zatrzymaną poprawkę wznów istniejącym przyciskiem.')
        package=verified_package(store,job_id)
        if state['request'].get('project')!='wealth_blueprint' or state['request'].get('delivery_mode')!='local_only':
            raise ProductionBlocked('Ta poprawka jest dostępna dla lokalnej rolki WB.')
        root=store.context(job_id).artifact_dir
        manifest=load_project_manifest(root/'project_manifest.json',verify_hash=True)
        if manifest.brand.visualVariant!='editorial_motion':
            raise ProductionBlocked('Ta poprawka zachowuje obecny kierunek editorial_motion. Inny styl wymaga osobnego planu.')
        # A real intact current review is required; a model PASS alone is insufficient.
        verified_current_renderer_review(store,state)
        from atnos.production.project_profile import content_hash
        from atnos.production.order_store import file_hash
        from atnos.production.channels.wealth_blueprint.remotion_order_edit import REMOTION_REQUIRED,RENDERER_BOUNDARY
        source=renderer_source_fingerprint()
        render=next((r for r in state['checkpoints'] if r['stage']=='remotion_render'),None)
        inputs={'manifest_sha256':file_hash(root/'project_manifest.json'),'renderer_source_fingerprint':source,
            'mode':REMOTION_REQUIRED,'renderer_boundary':RENDERER_BOUNDARY}
        if render and render['input_hash']==content_hash(inputs):
            raise ProductionBlocked('Ta rolka korzysta już z aktualnych kompozycji. Brak nowej zmiany do renderowania; nie uruchomiono powtórki.')
        result.update(can_repair=True,package_sha256=package['hash'],
            renderer_source=source,voice_id=state['profile']['production']['voice']['voice_id'])
    except (ProductionBlocked,ValueError,OSError,KeyError) as exc:
        result['reason']=str(exc)
    return result


async def register_studio_presentation(store,job_id,payload):
    from atnos.production.remotion_quality_repair import register_renderer_refresh,_refresh_rows
    from atnos.production.scoped_script_revision import verified_current_renderer_review
    state=store.get(job_id)
    previous=next((r for r in _refresh_rows(state) if r['output'].get('request_key')==payload.request_key),None)
    # Lost-response retries still reach the registrar's locked, immutable binding.
    if previous is None:
        snapshot=presentation_snapshot(store,job_id)
        if not snapshot['can_repair']:raise ProductionBlocked(snapshot['reason'])
        if snapshot['package_sha256']!=payload.expected_package_sha256:
            raise ProductionBlocked('Pakiet filmu zmienił się. Odczytaj ponownie przygotowanie poprawki.')
        proof,_=verified_current_renderer_review(store,state)
    else:
        proof=previous['output'].get('current_script_review')
    return await register_renderer_refresh(store,job_id,authorized=True,
        expected_package_sha256=payload.expected_package_sha256,reason=payload.note,
        visual_variant='editorial_motion',request_key=payload.request_key,
        expected_renderer_source=payload.expected_renderer_source,current_script_review=proof)
