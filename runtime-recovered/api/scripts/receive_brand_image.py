"""Return an externally generated asset against an exact Atnos request; never alter its pixels."""
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from atnos.core.config import get_settings
from atnos.production.order_store import ProductionOrderStore,file_hash
from atnos.production.project_profile import content_hash
from atnos.production.brand_contract import verified_asset,active_request

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    parser.add_argument('pose',choices=['thinking','explain','skeptical','pointing'])
    parser.add_argument('image',type=Path)
    parser.add_argument('--request-hash',required=True)
    args=parser.parse_args()
    store=ProductionOrderStore(get_settings())
    state=store.get(args.job_id)
    if state['worker_live'] or state['status']=='ready':
        raise ValueError('Do not change assets of a running or completed order')
    root=store.context(args.job_id).artifact_dir
    request=json.loads((root/'image_requests'/(args.pose+'.json')).read_text(encoding='utf-8'))
    request=active_request(root,args.pose,request)
    if content_hash(request)!=args.request_hash:
        raise ValueError('Wrong generation request hash')
    target=root/'brand_assets'/(request['asset_id']+'.png')
    receipt=target.with_suffix('.json')
    if target.exists() or receipt.exists():
        if verified_asset(root,request) and file_hash(target)==file_hash(args.image):
            print(json.dumps({'status':'already_received','path':str(target)}));return
        raise ValueError('Existing artwork is immutable; use an explicit revision instead')
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(args.image,target)
    payload={'request_hash':args.request_hash,'relative_file':target.relative_to(root).as_posix(),
             'sha256':file_hash(target),'provider':request['provider'],'transport':'supervised_tool_relay',
             'pixel_edits':'none','billing':request['billing']}
    receipt.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    verified_asset(root,request)
    print(json.dumps({'status':'received','path':str(target),'sha256':payload['sha256']}))

if __name__=='__main__':main()
