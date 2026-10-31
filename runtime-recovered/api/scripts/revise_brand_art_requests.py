"""Atnos art-director command: correct unusable simulated transparency before film composition."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from atnos.core.config import get_settings
from atnos.production.order_store import ProductionOrderStore,file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('job_id');p.add_argument('--identity-reference',type=Path,required=True)
a=p.parse_args();store=ProductionOrderStore(get_settings());state=store.get(a.job_id)
if state['worker_live'] or state['status']=='ready':raise ValueError('Order must be waiting for artwork')
root=store.context(a.job_id).artifact_dir
with store.lease(a.job_id) as token:
    for pose in ('thinking','explain','skeptical','pointing'):
        initial=json.loads((root/'image_requests'/(pose+'.json')).read_text(encoding='utf-8'))
        kit=state['profile']['brand_kit']['contract']
        req={**initial,'initial_request_hash':content_hash(initial),'output_mode':'navy_background',
             'identity_reference':str(a.identity_reference.resolve()),'identity_reference_sha256':file_hash(a.identity_reference),
             'correction':'The first output simulated transparency with a visible checkerboard. Use a plain navy illustrated background instead.',
             'prompt':('Image 1 is the supplied Wealth Blueprint visual reference. Image 2 is the generated Alex identity reference; '
                 'preserve his exact face, hair, clothing and illustration style. Create one full-body Alex in the required pose on a '
                 'COMPLETELY SOLID MATTE DARK NAVY background, exact color #142633. No transparency, checkerboard, paper wrinkles, grid, '
                 'words, props, cast shadows or floor. Portrait 2:3 framing, full body from hair to both complete sneakers with small margins. '
                 'The background must be uniform and featureless everywhere; skin and clothing retain their premium hand-drawn linework and soft volume. '
                 +kit['character']+' POSE: '+kit['poses'][pose])}
        inputs={'initial':content_hash(initial),'identity':req['identity_reference_sha256'],'correction_version':1}
        cached=store.begin(a.job_id,token,'image_revision/'+pose,inputs,external_tool_call=True)
        if cached is None:
            target=root/'image_revisions'/(pose+'.json');write_json(target,req)
            store.complete(a.job_id,token,'image_revision/'+pose,req,[target])
    store.fail(a.job_id,token,'external_images',category='awaiting_external_images',message='Atnos zlecił poprawione grafiki Alexa na jednolitym granacie; oczekuje zwrotu.')
print(json.dumps({'status':'four_revised_requests','job_id':a.job_id}))
