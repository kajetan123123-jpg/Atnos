"""Data-driven blueprint motion illustration template owned and executed by Atnos.

Character artwork is external generated RGBA; this module composes video frames,
typesets exact labels and animates native diagram geometry. It does not draw Alex.
"""
from __future__ import annotations
from functools import lru_cache
import json
import math
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image,ImageDraw,ImageFont
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.order_store import file_hash

W,H,FPS=720,1280,30

def ease(x):
    x=max(0,min(1,x));return x*x*(3-2*x)

def lerp(a,b,p):return a+(b-a)*ease(p)


class BlueprintFilm:
    def __init__(self,root,kit,script,voice,images):
        self.root,self.kit,self.script,self.voice=root,kit,script,voice
        self.p=kit['palette']
        self.font_root=root/'fonts';self.font_root.mkdir(exist_ok=True)
        source=Path('E:/Atnos/App/exports/three-channels-20260913/fonts')
        for name in ('georgia.ttf','seguisb.ttf','segoeui.ttf'):
            if not (self.font_root/name).is_file():shutil.copyfile(source/name,self.font_root/name)
        self.images={}
        for key,path in images.items():
            with Image.open(path) as im:
                im=im.convert('RGBA')
                # Composition trims empty transparent margins only, never alters character pixels.
                im=im.crop(im.getchannel('A').getbbox())
                self.images[key]=im.copy()
        self.dark=self.paper(False);self.light=self.paper(True)

    @lru_cache(maxsize=80)
    def font(self,size,style='regular'):
        return ImageFont.truetype(str(self.font_root/{'regular':'segoeui.ttf','bold':'seguisb.ttf','serif':'georgia.ttf'}[style]),size)

    def text(self,d,text,x,y,size=26,color=None,style='regular',anchor='mm'):
        d.text((x,y),text,font=self.font(size,style),fill=color or self.p['cream'],anchor=anchor)

    def line(self,d,points,fill=None,width=2):d.line(points,fill=fill or self.p['blue'],width=width,joint='curve')

    def paper(self,light):
        rng=np.random.default_rng(1409)
        base=np.array([237,225,204] if light else [19,36,48])
        noise=rng.normal(0,1.1,(H,W,1))
        y=np.linspace(0,1,H)[:,None,None]
        arr=np.clip(base[None,None,:]+noise+(3-y*7),0,255).astype('uint8')
        im=Image.fromarray(arr).convert('RGBA');d=ImageDraw.Draw(im)
        grid=(218,207,188) if light else (25,46,60)
        for x in range(0,W,44):d.line((x,0,x,H),fill=grid,width=1)
        for y in range(0,H,44):d.line((0,y,W,y),fill=grid,width=1)
        for r in (100,130,170):d.ellipse((-r,-r,r,r),outline=grid,width=1)
        return im

    def arrow(self,d,a,b,p=1,fill=None,width=2):
        p=ease(p);tip=(a[0]+(b[0]-a[0])*p,a[1]+(b[1]-a[1])*p)
        self.line(d,[a,tip],fill,width)
        angle=math.atan2(b[1]-a[1],b[0]-a[0])
        if p>.05:
            for offset in (-.55,.55):
                point=(tip[0]-12*math.cos(angle+offset),tip[1]-12*math.sin(angle+offset))
                self.line(d,[point,tip],fill,width)

    def actor(self,im,key,x,y,height,t):
        asset=self.images[key]
        h=round(height*(1+.0025*math.sin(t*1.8)));w=round(asset.width*h/asset.height)
        # A subtle camera float keeps identity stable; no claimed lip-sync or limb animation.
        asset=asset.resize((w,h),Image.Resampling.LANCZOS)
        im.alpha_composite(asset,(round(x-w/2),round(y-h+1.2*math.sin(t*1.5))))

    def token(self,d,x,y,size=40,kept=False,alpha=1):
        fill=self.p['gold'] if kept else self.p['cream']
        d.ellipse((x-size,y-size*.25+8,x+size,y+size*.35+8),fill='#101F28')
        d.rounded_rectangle((x-size,y-size*.52,x+size,y+size*.5),radius=10,fill=self.p['orange'] if kept else '#8E998F',outline=self.p['navy'],width=2)
        d.rounded_rectangle((x-size,y-size*.68,x+size,y+size*.23),radius=10,fill=fill,outline=self.p['navy'],width=2)
        if size>22:self.text(d,'1K',x,y-5,20,self.p['navy'],'bold')

    def seed(self, d, x, y, size=24):
        # A small asymmetric seed; retain gold as the single kept unit of value.
        points=[]
        curves=[((0,-1.2),(1.0,-.45),(1.1,.85),(0,1.0)),
                ((0,1.0),(-.85,.85),(-.72,-.65),(0,-1.2))]
        for a,b,c,e in curves:
            for i in range(25):
                t=i/24;u=1-t
                points.append((x+size*(u**3*a[0]+3*u*u*t*b[0]+3*u*t*t*c[0]+t**3*e[0]),
                               y+size*(u**3*a[1]+3*u*u*t*b[1]+3*u*t*t*c[1]+t**3*e[1])))
        d.polygon(points,fill=self.p['gold'])
        self.line(d,points+[points[0]],self.p['orange'],2)
        self.line(d,[(x-3,y+size*.6),(x+2,y),(x,y-size*.7)],self.p['cream'],2)

    def icon(self,d,kind,x,y,s=1,color=None):
        c=color or self.p['cream'];w=max(2,round(2*s))
        def line(pts):self.line(d,[(x+a*s,y+b*s) for a,b in pts],c,w)
        def box(box,r=4):d.rounded_rectangle(tuple((x+v*s if i%2==0 else y+v*s) for i,v in enumerate(box)),radius=r,outline=c,width=w)
        if kind=='housing':
            line([(-32,0),(0,-26),(32,0)]);line([(-24,-7),(-24,30),(24,30),(24,-7)]);line([(-6,30),(-6,10),(7,10),(7,30)])
        elif kind=='car':
            line([(-34,17),(-34,0),(-23,-6),(-14,-21),(15,-21),(27,-6),(35,0),(35,17),(-34,17)])
            for xx in (-20,21):d.ellipse((x+(xx-6)*s,y+12*s,x+(xx+6)*s,y+24*s),fill=self.p['navy'],outline=c,width=w)
        elif kind=='phone':
            box((-15,-31,15,31));line([(-5,-23),(5,-23)]);line([(-4,23),(4,23)])
        elif kind=='dining':
            d.ellipse((x-22*s,y-22*s,x+22*s,y+22*s),outline=c,width=w)
            line([(-32,-28),(-32,29)]);line([(-38,-28),(-38,-9),(-26,-9),(-26,-28)]);line([(33,-28),(33,29)])
        elif kind=='streaming':
            box((-30,-21,30,21));line([(-6,-11),(12,0),(-6,11),(-6,-11)])
        elif kind=='cloud':
            line([(-28,13),(-30,0),(-20,-9),(-8,-9),(-5,-21),(12,-24),(24,-12),(25,-4),(34,1),(32,14),(-28,13)])
        elif kind=='gym':
            line([(-31,0),(31,0)]);line([(-27,-18),(-27,18)]);line([(-18,-24),(-18,24)]);line([(18,-24),(18,24)]);line([(27,-18),(27,18)])
        elif kind=='app':
            box((-23,-23,23,23),8);line([(-9,8),(0,-12),(10,8)]);line([(-6,2),(7,2)])
        elif kind=='delivery':
            box((-23,-21,23,23));line([(-23,-21),(0,-31),(23,-21)]);line([(0,-31),(0,-9)]);line([(-23,-21),(0,-9),(23,-21)])
        elif kind=='shield':
            line([(0,-29),(27,-18),(24,12),(0,31),(-24,12),(-27,-18),(0,-29)]);line([(-10,0),(-2,9),(13,-9)])
        elif kind=='investments':
            line([(-29,27),(-29,-26)]);line([(-29,27),(30,27)])
            for xx,hh in ((-17,8),(0,23),(17,40)):box((xx-5,22-hh,xx+5,22),1)
        elif kind=='goals':
            for r in (12,25):d.ellipse((x-r*s,y-r*s,x+r*s,y+r*s),outline=c,width=w)
            line([(0,0),(29,-29)]);line([(20,-28),(29,-29),(28,-20)])

    def caption(self,d,t):
        cue=next((c for c in self.voice['cues'] if c['start']<=t<c['end']),None)
        if not cue:return
        lines=[];line=''
        for word in cue['text'].split():
            candidate=(line+' '+word).strip()
            if self.font(33,'bold').getlength(candidate)>590 and line:lines.append(line);line=word
            else:line=candidate
        if line:lines.append(line)
        top=1058
        d.rounded_rectangle((40,top-18,680,top+len(lines)*44+7),radius=14,fill='#132632')
        for i,txt in enumerate(lines):self.text(d,txt,360,top+19+i*44,33,self.p['cream'],'bold')

    def frame(self,t):
        scene=next(s for s in self.script['segments'] if s['start']<=t<s['end'])
        v=scene['visual'];local=t-scene['start'];dur=scene['end']-scene['start'];p=local/dur
        light=v in ('flows','seed')
        im=(self.light if light else self.dark).copy();d=ImageDraw.Draw(im)
        ink=self.p['navy'] if light else self.p['cream']
        self.text(d,'WEALTH BLUEPRINT',52,65,18,ink,'bold',anchor='lm')
        self.line(d,[(52,93),(668,93)],self.p['blue'],1)
        self.text(d,'01 / THE RAISE',668,65,13,ink,anchor='rm')
        if v=='raise':
            self.text(d,'A bigger paycheck.',360,155,34,ink,'serif')
            self.text(d,'A richer life?',360,209,46,ink,'serif')
            amount='5,000' if local<2.5 else '7,000'
            self.text(d,amount,360,359,103,self.p['gold'] if local>=2.5 else ink,'serif')
            self.text(d,'PLN / MONTH',360,432,18,self.p['orange'],'bold')
            self.actor(im,'thinking',360,1030,565,t);d=ImageDraw.Draw(im)
        elif v=='tokens':
            self.text(d,'Not necessarily.',360,164,47,ink,'serif')
            self.text(d,'BREAK IT DOWN',360,233,18,self.p['gold'],'bold')
            for i in range(7):
                x=120+(i%4)*160 if i<4 else 200+(i-4)*160
                y=340 if i<4 else 440
                if local>i*.18:self.token(d,lerp(360,x,(local-i*.18)/.7),lerp(315,y,(local-i*.18)/.7),33)
            self.text(d,'7 TOKENS × PLN 1,000',360,523,18,ink)
            self.actor(im,'explain',370,1042,480,t);d=ImageDraw.Draw(im)
        elif v=='costs':
            self.text(d,'Income rises.',360,164,42,ink,'serif')
            self.text(d,'So can spending.',360,218,43,ink,'serif')
            destinations=[(127,356,'housing','HOME'),(282,356,'car','CAR'),(437,356,'phone','PHONE'),(592,356,'dining','DINING')]
            for x,y,kind,label in destinations:
                self.icon(d,kind,x,y,.88);self.text(d,label,x,y+60,16,ink,'bold')
            for i in range(7):
                start=(90+i*90,535)
                dest=destinations[max(0,i-1)][:2] if i<5 else start
                if i==0:dest=(110,443)
                elif i==1:dest=(144,452)
                elif i<5:dest=(dest[0],443)
                progress=ease((local-.65-i*.7)/.6) if i<5 else 0
                self.token(d,lerp(start[0],dest[0],progress),lerp(start[1],dest[1],progress),24 if progress>.9 else 27)
            self.text(d,'ILLUSTRATIVE BUDGET',360,612,15,self.p['blue'],'bold')
            self.actor(im,'explain',362,1052,412,t);d=ImageDraw.Draw(im)
        elif v=='extras':
            self.text(d,'Then the little things.',360,166,40,ink,'serif')
            for i,kind in enumerate(('streaming','app','gym','cloud','delivery')):
                x=96+i*132;self.icon(d,kind,x,315,.68);self.text(d,kind.upper(),x,365,13,ink,'bold')
            self.line(d,[(85,400),(85,416),(630,416),(630,400)],self.p['blue'],2)
            self.text(d,'SMALL COSTS ADD UP',360,459,18,self.p['orange'],'bold')
            if local<2.7:self.token(d,lerp(285,360,(local-.3)/2),lerp(566,420,(local-.3)/2),round(lerp(34,10,(local-.3)/2)))
            self.token(d,437,566,34,kept=True)
            self.actor(im,'skeptical',330,1050,440,t);d=ImageDraw.Draw(im)
        elif v=='flows':
            self.text(d,'Where does it go?',360,177,46,ink,'serif')
            self.text(d,'EARN',360,354,31,ink,'bold')
            self.arrow(d,(326,387),(195,526),local/1.2,self.p['blue'],3)
            self.arrow(d,(394,387),(525,526),(local-.5)/1.2,self.p['teal'],3)
            self.text(d,'SPEND',184,567,34,ink,'bold');self.text(d,'KEEP',532,567,34,ink,'bold')
            for i in range(6):self.token(d,135+i%3*48,664+i//3*48,21)
            self.token(d,530,690,43,True)
            self.text(d,'The amount that stays.',360,854,33,ink,'serif')
            self.line(d,[(94,929),(626,929)],self.p['blue'],1)
        elif v=='seed':
            self.text(d,'Give the gap a job.',360,175,45,ink,'serif')
            self.text(d,'WHAT YOU KEEP',360,276,19,self.p['teal'],'bold')
            progress=ease((local-.7)/2)
            if progress<.85:
                self.token(d,360,540,round(lerp(62,18,progress)),True)
            else:
                self.seed(d,360,540,lerp(18,26,(progress-.85)/.15))
            if progress>.6:
                self.arrow(d,(360,650),(360,586),(local-2)/1.3,self.p['teal'],3)
                self.line(d,[(110,711),(610,711)],self.p['blue'],2)
                self.text(d,'AND WHAT IT DOES NEXT',360,804,25,ink,'bold')
            self.text(d,'A starting point.',360,920,31,ink,'serif')
        elif v=='branches':
            self.text(d,'Build with intention.',360,167,43,ink,'serif')
            self.seed(d,360,560,26)
            targets=[(145,353,'shield','EMERGENCY','FUND'),(360,322,'investments','INVESTMENTS',''),(575,353,'goals','GOALS','')]
            for i,(x,y,kind,a,b) in enumerate(targets):
                self.arrow(d,(360,524),(x,y+112),(local-.25-i*.3)/1.4,self.p['gold'],2)
                self.icon(d,kind,x,y,.83,self.p['cream'])
                self.text(d,a,x,y+60,17,ink,'bold')
                if b:self.text(d,b,x,y+84,17,ink,'bold')
            self.actor(im,'pointing',445,1050,432,t);d=ImageDraw.Draw(im)
        elif v=='gap':
            self.text(d,'It starts here.',360,169,50,ink,'serif')
            for i,(label,x) in enumerate((('INCOME',139),('GAP',361),('WEALTH',582))):
                c=self.p['gold'] if label=='GAP' else self.p['blue']
                d.rounded_rectangle((x-86,314,x+86,401),radius=8,outline=c,width=3)
                self.text(d,label,x,357,27,self.p['gold'] if label=='GAP' else ink,'bold')
                if i<2:self.arrow(d,(x+88,357),(x+127,357),local/.7,self.p['blue'],2)
            if local>.7:
                self.line(d,[(305,423),(417,420)],self.p['gold'],3)
                self.text(d,'WHAT YOU KEEP',361,463,16,self.p['gold'],'bold')
            self.actor(im,'pointing',426,1040,500,t);d=ImageDraw.Draw(im)
        else:
            # Drafting lines resolve into the wordmark; no arbitrary transition effect.
            cx,cy=360,352
            for r in (57,65):d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=self.p['gold'],width=2)
            for a in range(0,360,90):
                theta=math.radians(a);ex=cx+78*math.cos(theta);ey=cy+78*math.sin(theta)
                self.line(d,[(cx,cy),(lerp(cx,ex,local/.8),lerp(cy,ey,local/.8))],self.p['gold'],2)
            self.text(d,'WEALTH',360,533,72,ink,'serif')
            self.text(d,'B L U E P R I N T',360,611,31,ink)
            self.line(d,[(135,674),(585,674)],self.p['gold'],2)
            self.text(d,'Understand money.',360,756,33,ink,'serif')
            self.text(d,'Build intentionally.',360,806,33,ink,'serif')
        self.caption(d,t)
        # Deliberately below the caption zone and inside mobile action-button clearance.
        self.text(d,'WEALTH BLUEPRINT',360,1193,12,self.p['blue'],'bold')
        return im.convert('RGB')


def render(root,kit,script,voice,images):
    film=BlueprintFilm(root,kit,script,voice,images)
    output=root/'short.mp4';frames=root/'frames';frames.mkdir(exist_ok=True)
    ff=resolve_ffmpeg()
    command=[str(ff),'-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-',
             '-i',str(root/'mix.wav'),'-vf','scale=1080:1920:flags=lanczos','-c:v','libx264','-preset','fast','-crf','18',
             '-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart','-t','45',str(output)]
    samples={round((s['start']+min(2,(s['end']-s['start'])/2))*FPS):s['id'] for s in script['segments']}
    with (root/'render.log').open('w',encoding='utf-8') as log:
        p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for index in range(45*FPS):
                frame=film.frame(index/FPS)
                p.stdin.write(frame.tobytes())
                if index in samples:frame.resize((1080,1920),Image.Resampling.LANCZOS).save(frames/(samples[index]+'.png'))
            p.stdin.close()
            if p.wait(timeout=180):raise RuntimeError('Film encoding failed; see render.log')
        finally:
            if p.poll() is None:p.kill();p.wait()
    return {'output_file':str(output),'sha256':file_hash(output),'width':1080,'height':1920,'fps':30,'duration_seconds':45,
            'composition_resolution':[W,H],'delivery_resolution':[1080,1920],
            'render_type':'motion illustration with animated diagrams','unique_character_artworks':len({file_hash(p) for p in images.values()}),
            'character_lip_sync':False,'narration_source':'Atnos generated English script and local Kokoro',
            'source_assets':{key:file_hash(value) for key,value in images.items()}}
