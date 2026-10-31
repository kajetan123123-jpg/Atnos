"""Read-only inventory/contact board helpers; these never set asset approval."""
import json, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from atnos.production.project_manifest import file_sha256

def contact_board(rows, destination: Path, *, title='WB CANDIDATES - USER APPROVAL PENDING'):
    width,cell=1500,300
    board=Image.new('RGB',(width,90+math.ceil(len(rows)/5)*360),'#142633')
    draw=ImageDraw.Draw(board)
    fontpath=Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/fonts/Montserrat-Bold.ttf')
    font=ImageFont.truetype(str(fontpath),18)
    draw.text((24,24),title,fill='#F0E5D2',font=font)
    for i,row in enumerate(rows):
        x,y=(i%5)*cell,90+(i//5)*360
        path=Path(row['path'])
        if path.is_file():
            try:
                with Image.open(path) as image:
                    image=ImageOps.contain(image.convert('RGBA'),(280,280))
                    board.paste(image,(x+(cell-image.width)//2,y),image)
            except Exception:pass
        draw.text((x+10,y+287),str(row.get('id') or row.get('role'))[:25],fill='#F0E5D2',font=font)
        draw.text((x+10,y+316),row.get('review_status','PENDING')[:24],fill='#D5AD6C',font=font)
    destination.parent.mkdir(parents=True,exist_ok=True);board.save(destination)
    return str(destination)

def inventory_library(catalog_path: Path, destination: Path, *, previous_review=None):
    catalog=json.loads(catalog_path.read_text(encoding='utf-8'))
    previous=json.loads(Path(previous_review).read_text(encoding='utf-8')) if previous_review else {}
    prior={r['sha256']:r for r in previous.get('visuals',[]) if r.get('sha256')}
    rows=[]
    for record in catalog['assets']:
        path=Path(record['file_path'])
        if not path.is_absolute():
            candidates=[catalog_path.parent/path,Path('E:/Atnos/App/config')/path]
            path=next((p for p in candidates if p.is_file()),candidates[0])
        sha=file_sha256(path)
        role=record.get('production_role')
        status='REQUIRES_VISUAL_REVIEW'
        reason='approved flag alone does not establish fitness or user approval'
        if sha in prior:
            status=prior[sha].get('review_status',status);reason=prior[sha].get('review_reason',reason)
        elif role=='reference_sheet':status='REFERENCE_ONLY'
        elif record.get('quality_status')=='REJECTED' or 'placeholder' in str(path).casefold():status='REJECTED'
        if not sha:status='MISSING'
        rows.append({'id':record['id'],'path':str(path),'sha256':sha,'role':role,'catalog_approved':record.get('approved'),
                     'review_status':status,'reason':reason,'user_approval_inferred':False})
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'library-audit.json').write_text(json.dumps({'catalog_sha256':file_sha256(catalog_path),'assets':rows},indent=2),encoding='utf-8')
    changed=[row for row in rows if row['sha256'] not in prior]
    contact_board(changed,destination/'library-new-assets.jpg',title='WB LIBRARY - NEW OR CHANGED ASSETS, NOT APPROVAL')
    return rows
