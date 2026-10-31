"""Record the user's exact local single-pose authorization for an existing order."""
import argparse,json,shutil,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from atnos.core.config import get_settings
from atnos.production.order_store import ProductionOrderStore,file_hash
from atnos.production.steps.common import write_json
p=argparse.ArgumentParser(description=__doc__);p.add_argument('job_id');p.add_argument('source',type=Path)
a=p.parse_args();store=ProductionOrderStore(get_settings());state=store.get(a.job_id)
if state['worker_live'] or state['status']=='ready':raise ValueError('Wait for the current worker before changing trial scope')
root=store.context(a.job_id).artifact_dir
target=root/'brand_assets/alex_original.png';target.parent.mkdir(parents=True,exist_ok=True)
if target.exists() and file_hash(target)!=file_hash(a.source):raise ValueError('Existing source image differs')
shutil.copyfile(a.source,target)
payload={'job_id':a.job_id,'authorization':'Tak, zrób lokalną próbę z jednym Alexem',
         'scope':'local background removal and single-pose motion illustration trial',
         'source_relative_file':target.relative_to(root).as_posix(),'source_sha256':file_hash(target),
         'initial_generation':'Codex imagegen supervised relay; output had a simulated checkerboard background',
         'additional_poses':'deferred after generator returned usage_limit_reached'}
out=root/'single-pose-authorization.json'
if out.exists() and json.loads(out.read_text(encoding='utf-8'))!=payload:raise ValueError('Existing authorization differs')
write_json(out,payload);print(json.dumps({'status':'authorization_recorded','path':str(out)}))
