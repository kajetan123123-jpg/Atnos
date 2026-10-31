"""Create a local-only WB review fragment through production services."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from atnos.production.channels.wealth_blueprint.preview import preview_plan,render_review_fragment
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',required=True,type=Path)
parser.add_argument('--output',type=Path)
parser.add_argument('--pack',type=Path)
parser.add_argument('--plan-only',action='store_true')
parser.add_argument('--segments',nargs=3,default=['s03','s04','s05'])
args=parser.parse_args()
if args.plan_only:
    print(json.dumps(preview_plan(args.source,args.segments)[2],indent=2))
else:
    if args.output is None:parser.error('--output is required for rendering')
    print(json.dumps(render_review_fragment(args.source,args.output,selected_ids=args.segments,illustration_pack_path=args.pack),indent=2))
