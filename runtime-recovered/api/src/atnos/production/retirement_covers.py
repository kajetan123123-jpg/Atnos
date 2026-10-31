"""Visible retirement-account documents, rather than a text-only tax thumbnail."""
from pathlib import Path

from PIL import Image, ImageDraw

from atnos.production.editorial_film import BG, INK, MINT, MUTED, ORANGE, _text, _wrapped


def create(directory: Path, *, headline: str, detail: str, claims=()):
    directory.mkdir(parents=True,exist_ok=True)
    paths=[]
    for width,height,name in [(1080,1920,"cover_portrait.png"),(1280,720,"thumbnail_landscape.png")]:
        portrait=height>width
        image=Image.new("RGB",(width,height),BG)
        draw=ImageDraw.Draw(image)
        for x in range(-height,width,145):
            draw.line((x,height,x+height,0),fill="#26372D",width=2)
        _text(draw,(68,68),"WEALTH BLUEPRINT",size=28,fill=MUTED)
        _wrapped(draw,(68,275 if portrait else 155),headline,width=944 if portrait else 650,
                 size=115 if portrait else 74,max_lines=4,fill=MINT)
        x,y,w,h=(68,850,944,610) if portrait else (790,160,420,360)
        gap=28 if portrait else 18
        card=(w-gap)/2
        for index,label in enumerate(("IKE","IKZE")):
            left=x+index*(card+gap)
            right=left+card
            bottom=y+h*.83
            draw.rounded_rectangle((left,y,right,bottom),radius=15,fill=INK)
            inset=30 if portrait else 14
            fold=50 if portrait else 25
            draw.polygon(((right-fold,y),(right-fold,y+fold),(right,y+fold)),fill="#BCC7BD")
            _text(draw,(left+inset,y+30),label,size=88 if portrait else 42,fill=BG,max_width=card-inset*2)
            for n,term in enumerate(("AGE","HISTORY")):
                yy=y+h*.32+n*h*.14
                size=28 if portrait else 15
                draw.rectangle((left+inset,yy,left+inset+size,yy+size),outline="#477F4A",width=3)
                draw.line((left+inset+5,yy+size*.5,left+inset+size*.45,yy+size-5,left+inset+size-4,yy+4),fill="#477F4A",width=3)
                _text(draw,(left+inset+size+12,yy-2),term,size=38 if portrait else 19,fill=BG,max_width=card-inset*2-size-12)
            draw.line((left+inset,bottom-50,right-inset,bottom-50),fill="#B5BFB7",width=3)
            draw.line((left+inset,bottom-30,right-inset-20,bottom-30),fill="#B5BFB7",width=3)
        _text(draw,(x,y+h*.91),"CHECK YOUR WITHDRAWAL CONDITIONS",size=40 if portrait else 21,fill=ORANGE,max_width=w)
        _wrapped(draw,(68,1600 if portrait else 602),detail,width=width-136,size=46 if portrait else 30,
                 max_lines=3 if portrait else 2,fill=INK,serif=False)
        path=directory/name
        image.save(path)
        paths.append(path)
    return tuple(paths)
