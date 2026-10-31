"""A concrete, data-backed bank/receipt illustration for purchasing-power covers."""
from pathlib import Path

from PIL import Image, ImageDraw

from atnos.production.editorial_film import BG, INK, MINT, MUTED, ORANGE, _text, _wrapped
from atnos.production.steps.order_script import NumericClaim, calculate_claim


def create(directory: Path, *, headline: str, detail: str, claims: list[dict]):
    values = {row["id"]: NumericClaim.model_validate(row) for row in claims}
    interest, prices, loss = (values[key] for key in ("deposit_interest","price_increase","real_purchasing_loss"))
    if any(abs(calculate_claim(row)-row.result) > .001 for row in (interest,prices,loss)):
        raise ValueError("Cover requires verified calculations")
    if interest.operation != "percent" or prices.operation != "percent":
        raise ValueError("Cover rates require explicit percentage inputs")
    rate, inflation = interest.operands[1],prices.operands[1]
    directory.mkdir(parents=True,exist_ok=True)
    paths = []
    for width,height,name in [(1080,1920,"cover_portrait.png"),(1280,720,"thumbnail_landscape.png")]:
        portrait = height>width
        image = Image.new("RGB",(width,height),BG)
        draw = ImageDraw.Draw(image)
        for x in range(-height,width,145):
            draw.line((x,height,x+height,0),fill="#26372D",width=2)
        _text(draw,(68,68),"WEALTH BLUEPRINT",size=28,fill=MUTED)
        _wrapped(draw,(68,275 if portrait else 160),headline,width=944 if portrait else 650,
            size=115 if portrait else 74,max_lines=4 if portrait else 4,fill=MINT)
        x,y,w,h = (68,860,944,570) if portrait else (790,165,420,340)
        _text(draw,(x,y-65 if portrait else y-40),"ILLUSTRATIVE EXAMPLE",size=32 if portrait else 19,fill=MUTED,max_width=w)
        gap = 28 if portrait else 16
        card = (w-gap)/2
        for index,(label,amount) in enumerate([("BANK INTEREST",rate),("SHOPPING PRICES",inflation)]):
            left=x+index*(card+gap)
            right=left+card
            bottom=y+h*.78
            draw.rounded_rectangle((left,y,right,bottom),radius=22 if portrait else 14,fill=INK)
            inset = 32 if portrait else 18
            # Paper-like line items visually distinguish the statement and shopping receipt.
            _text(draw,(left+inset,y+inset),label,size=32 if portrait else 17,fill=BG,max_width=card-2*inset)
            for row,portion in enumerate((.65,.85,.52)):
                yy=y+h*.20+row*h*.065
                draw.line((left+inset,yy,left+inset+(card-2*inset)*portion,yy),fill="#B8C0B4",width=5 if portrait else 3)
            _text(draw,(left+inset,y+h*.46),f"+{amount:g}%",size=100 if portrait else 62,fill=BG,max_width=card-2*inset)
            draw.line((left+inset,bottom-26,right-inset,bottom-26),fill=ORANGE if index else "#477F4A",width=8 if portrait else 5)
        _text(draw,(x,y+h*.87),f"REAL CHANGE: -{loss.result:.2f}% BEFORE TAX",size=34 if portrait else 20,fill=MINT,max_width=w)
        _wrapped(draw,(68,1600 if portrait else 602),detail,width=width-136,size=46 if portrait else 30,
            max_lines=3 if portrait else 2,fill=INK,serif=False)
        path = directory/name
        image.save(path)
        paths.append(path)
    return tuple(paths)
