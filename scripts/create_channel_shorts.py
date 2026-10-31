"""Render three authored English shorts through Atnos local voice and original 2D artwork.

This is an assisted production runner, not an assertion that the fiction-order pipeline
already supports full films. Inputs, audio, captions, checkpoints and exports stay on E:.
"""
import argparse
from bisect import bisect_right
from difflib import SequenceMatcher
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp', PYTHONIOENCODING='utf-8',
                  HF_HOME='E:/Atnos/Storage/ToolCaches/huggingface', XDG_CACHE_HOME='E:/Atnos/Storage/ToolCaches',
                  USERPROFILE='E:/Atnos/Profiles/AtnosSystem', APPDATA='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming',
                  LOCALAPPDATA='E:/Atnos/Profiles/AtnosSystem/AppData/Local')
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from atnos.core.config import get_settings
from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeService
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService

ROOT = Path('E:/Atnos/App/exports/three-channels-20260913')
SPEC = Path('E:/Atnos/App/config/shorts-three-channels.json')
FFMPEG = 'E:/Atnos/Tools/FFmpeg/bin/ffmpeg.exe'
FFPROBE = 'E:/Atnos/Tools/FFmpeg/bin/ffprobe.exe'
W, H, FPS = 720, 1280, 30
FONTS = ROOT / 'fonts'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, timeout=180):
    with log.open('w', encoding='utf-8') as handle:
        result = subprocess.run(command, stdout=handle, stderr=handle, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed; inspect ' + str(log))


@lru_cache(maxsize=80)
def font(size, style='bold'):
    return ImageFont.truetype(str(FONTS / {'bold':'seguisb.ttf', 'regular':'segoeui.ttf', 'serif':'georgia.ttf'}[style]), size)


def centered(draw, text, y, size=42, fill='#f5f0df', style='bold', x=W/2):
    f=font(size,style)
    draw.text((x,y),text,font=f,fill=fill,anchor='mt')


def wrap(text, max_width, size, style='bold'):
    f=font(size,style); lines=[]; line=''
    for word in text.split():
        candidate=(line+' '+word).strip()
        if f.getlength(candidate)>max_width and line:
            lines.append(line); line=word
        else: line=candidate
    if line: lines.append(line)
    return lines


def ease(x):
    x=max(0,min(1,x)); return x*x*(3-2*x)


def gradient(top,bottom):
    a=np.array(top); b=np.array(bottom)
    y=np.linspace(0,1,H)[:,None,None]
    arr=np.repeat((a[None,None,:]*(1-y)+b[None,None,:]*y).astype(np.uint8),W,axis=1)
    return Image.fromarray(arr).convert('RGBA')


def glow(im, x,y,r,color,opacity=100):
    # Pre-rendered glows are cached; only original artwork is composited here.
    key=(r,color,opacity)
    if key not in GLOWS:
        g=Image.new('RGBA',(r*2,r*2))
        d=ImageDraw.Draw(g)
        for radius in range(r,0,-3):
            a=int(opacity*(1-radius/r)**2)
            d.ellipse((r-radius,r-radius,r+radius,r+radius),fill=(*color,a))
        GLOWS[key]=g
    im.alpha_composite(GLOWS[key],(int(x-r),int(y-r)))


GLOWS={}


def fox_sprite(sleeping=False, blink=False):
    im=Image.new('RGBA',(300,330)); d=ImageDraw.Draw(im)
    # A curved, cream-tipped tail and rounded silhouette give every scene the same fox.
    d.ellipse((135,182,297,291),fill='#c76b35')
    d.polygon([(240,182),(285,194),(294,229),(265,221),(247,235),(241,213)],fill='#f6dbad')
    d.ellipse((53,162,205,308),fill='#db7f3f')
    d.ellipse((73,183,177,299),fill='#f4c58b')
    d.ellipse((45,283,113,320),fill='#9c4c30'); d.ellipse((149,281,209,318),fill='#9c4c30')
    d.polygon([(43,83),(31,12),(98,46)],fill='#d97839'); d.polygon([(171,47),(232,11),(217,94)],fill='#d97839')
    d.polygon([(47,66),(45,30),(79,48)],fill='#754232'); d.polygon([(189,50),(218,29),(212,69)],fill='#754232')
    d.ellipse((36,42,232,213),fill='#e8964d')
    d.polygon([(45,119),(133,173),(222,118),(204,182),(135,218),(60,180)],fill='#ffe4b7')
    if blink or sleeping:
        d.arc((74,101,104,127),0,180,fill='#322f2a',width=5); d.arc((164,101,194,127),0,180,fill='#322f2a',width=5)
    else:
        d.ellipse((85,106,97,125),fill='#322f2a'); d.ellipse((170,106,182,125),fill='#322f2a')
        d.ellipse((88,107,92,111),fill='white'); d.ellipse((173,107,177,111),fill='white')
    d.ellipse((119,150,145,168),fill='#3b3430')
    d.arc((111,154,153,183),5,175,fill='#674435',width=3)
    d.ellipse((65,133,86,145),fill='#edb084'); d.ellipse((187,133,208,145),fill='#edb084')
    d.polygon([(99,47),(131,18),(190,21),(168,45),(134,56)],fill='#8aaf63')
    d.line([(104,48),(157,30),(181,25)],fill='#456a4c',width=3)
    return im


def owl_sprite(blink=False):
    im=Image.new('RGBA',(240,285)); d=ImageDraw.Draw(im)
    d.ellipse((31,73,207,267),fill='#819a9b')
    d.ellipse((52,118,184,258),fill='#b9c6bb')
    d.polygon([(31,94),(29,14),(94,49),(168,48),(210,13),(209,114)],fill='#819a9b')
    d.ellipse((26,47,126,157),fill='#e4e0c6'); d.ellipse((116,47,216,157),fill='#e4e0c6')
    for x in (76,167):
        d.ellipse((x-23,74,x+23,124),fill='#d6b253')
        if blink: d.line((x-16,103,x+16,103),fill='#283737',width=4)
        else:
            d.ellipse((x-10,83,x+10,119),fill='#273939'); d.ellipse((x-4,85,x+2,92),fill='white')
    d.polygon([(111,123),(132,123),(122,146)],fill='#d99542')
    d.rounded_rectangle((52,151,188,178),12,fill='#5d9aba');d.polygon([(149,167),(172,169),(185,221),(157,224)],fill='#5d9aba')
    d.ellipse((15,141,64,236),fill='#6d868a'); d.ellipse((179,141,227,236),fill='#6d868a')
    for x,y in [(86,198),(114,220),(141,198)]:d.arc((x-8,y-4,x+8,y+8),0,180,fill='#7d9792',width=3)
    d.ellipse((57,251,101,278),fill='#d99f55');d.ellipse((139,251,183,278),fill='#d99f55')
    return im


def sprite(im, asset,x,y,scale=1,flip=False,angle=0):
    a=asset
    if flip:a=a.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if scale!=1:a=a.resize((int(a.width*scale),int(a.height*scale)),Image.Resampling.LANCZOS)
    if angle:a=a.rotate(angle,resample=Image.Resampling.BICUBIC,expand=True)
    im.alpha_composite(a,(int(x-a.width/2),int(y-a.height)))


def lantern(im,x,y,scale=1,brightness=1):
    glow(im,x,y, int(110*scale),(255,196,89),int(110*brightness))
    d=ImageDraw.Draw(im); s=scale
    d.arc((x-17*s,y-55*s,x+17*s,y-24*s),180,360,fill='#d9bc74',width=max(2,int(5*s)))
    d.rounded_rectangle((x-25*s,y-32*s,x+25*s,y+38*s),int(8*s),fill='#dcb969')
    d.rounded_rectangle((x-18*s,y-23*s,x+18*s,y+27*s),int(4*s),fill='#ffe5a1')
    d.line((x,y-22*s,x,y+29*s),fill='#c79e50',width=max(2,int(4*s)))
    d.ellipse((x-9*s,y-8*s,x+9*s,y+16*s),fill='#fff6d1')


def forest_bg(night=False):
    im=gradient((24,51,66) if night else (41,73,79),(48,83,68) if night else (134,147,106))
    d=ImageDraw.Draw(im)
    d.ellipse((485,210,575,300),fill='#e4dcb1')
    rng=np.random.default_rng(9)
    for x,y,r in zip(rng.integers(40,680,55),rng.integers(120,710,55),rng.integers(1,3,55)):
        d.ellipse((int(x-r),int(y-r),int(x+r),int(y+r)),fill='#c6d9bb')
    for x,y,s,c in [(40,880,1.3,'#284e52'),(173,830,.9,'#375e59'),(641,905,1.2,'#2e5551'),(556,875,.75,'#42675b')]:
        d.polygon([(x-27*s,y),(x-18*s,y-600*s),(x+12*s,y-670*s),(x+36*s,y)],fill=c)
        for yy,xx in [(y-400*s,x-130*s),(y-510*s,x+110*s),(y-610*s,x-70*s)]:
            d.ellipse((xx-170*s,yy-115*s,xx+160*s,yy+120*s),fill=c)
    d.ellipse((-350,735,1070,1560),fill='#365c4c' if night else '#547957')
    d.polygon([(180,1050),(355,785),(401,773),(448,861),(644,1050)],fill='#708363' if night else '#bdba85')
    d.ellipse((-320,947,470,1475),fill='#254e43');d.ellipse((405,967,1050,1438),fill='#274e41')
    for x in range(15,700,37):
        y=990+(x*13%50); d.line((x,y,x-8,y-35),fill='#60815a',width=3);d.line((x,y,x+12,y-22),fill='#60815a',width=3)
    return im


def tree_house(im):
    d=ImageDraw.Draw(im)
    d.polygon([(444,940),(443,485),(474,235),(576,190),(607,530),(629,939)],fill='#735f48')
    d.line([(493,878),(481,493),(514,300)],fill='#927a57',width=8)
    d.line([(575,873),(560,652),(581,450)],fill='#574e40',width=7)
    d.rounded_rectangle((461,525,589,731),58,fill='#293d39',outline='#a08c63',width=10)
    d.rounded_rectangle((448,715,611,744),10,fill='#a2946b')
    for y in (792,840,889): d.rounded_rectangle((435,y,611,y+18),7,fill='#a2946b')
    d.line((460,744,451,910),fill='#a2946b',width=7);d.line((590,744,590,913),fill='#a2946b',width=7)


def key(im,x,y,scale=1,color='#dcc087',angle=0,tooth=False):
    a=Image.new('RGBA',(130,250));d=ImageDraw.Draw(a)
    if tooth:
        d.polygon([(34,37),(75,23),(108,50),(94,92),(82,210),(58,175),(47,104),(25,77)],fill=color)
        d.line([(43,55),(65,43),(81,51)],fill='#aaa299',width=4)
    else:
        d.ellipse((22,12,109,100),fill=color);d.ellipse((43,31,89,78),fill='#223539')
        d.rounded_rectangle((55,76,76,222),6,fill=color)
        d.rectangle((74,169,105,187),fill=color);d.rectangle((74,207,101,224),fill=color)
    sprite(im,a,x,y,scale=scale,angle=angle)


def hallway_bg():
    im=gradient((10,17,24),(17,31,38));d=ImageDraw.Draw(im)
    d.polygon([(0,150),(173,298),(173,950),(0,1100)],fill='#203038')
    d.polygon([(720,150),(553,298),(553,950),(720,1100)],fill='#263338')
    d.polygon([(0,1100),(175,946),(549,946),(720,1100),(720,1280),(0,1280)],fill='#1a242e')
    for x in (0,120,290,480,670,850):d.line((365,875,x,1280),fill='#33404a',width=2)
    for y in (990,1045,1120,1230):d.line((0,y,720,y),fill='#303d44',width=2)
    d.rectangle((171,288,557,960),fill='#0b111a',outline='#536068',width=7)
    d.rectangle((192,311,535,950),fill='#253337',outline='#768078',width=3)
    for x in (213,305,424,507):d.line((x,333,x,928),fill='#354345',width=2)
    d.rounded_rectangle((298,350,422,405),5,fill='#111e25',outline='#807e67',width=2)
    centered(d,'404',357,31,fill='#c4c1a4')
    d.ellipse((351,467,370,486),fill='#080e15',outline='#a19877',width=3)
    d.rounded_rectangle((465,670,493,726),5,fill='#777b70')
    d.ellipse((471,681,487,698),fill='#131f25')
    d.rounded_rectangle((279,791,449,829),5,fill='#10171e',outline='#626c65',width=2)
    glow(im,362,235,180,(143,171,134),40)
    d=ImageDraw.Draw(im);d.rounded_rectangle((302,222,422,238),6,fill='#ccd1b4')
    return im


@lru_cache(maxsize=1)
def palm_sprite():
    im=Image.new('RGBA',(360,500));d=ImageDraw.Draw(im)
    skin='#aa978a';shadow='#887a73'
    d.rounded_rectangle((114,340,272,500),28,fill=shadow)
    for box in [(60,66,121,302),(130,22,193,278),(202,45,261,301),(270,113,321,319)]:
        d.rounded_rectangle(box,28,fill=skin)
    d.ellipse((58,197,329,408),fill=skin)
    d.line((96,342,32,242),fill=skin,width=58)
    d.ellipse((3,213,61,271),fill=skin)
    d.arc((93,247,262,351),20,143,fill=shadow,width=4)
    d.arc((82,288,249,397),205,280,fill=shadow,width=3)
    for x,y in [(89,174),(162,151),(231,174),(293,216)]:d.line((x-12,y,x+12,y),fill=shadow,width=2)
    return im


def finance_scene(index,p,t):
    im=BACKGROUNDS['finance'].copy();d=ImageDraw.Draw(im)
    for x in range(48,720,70):d.line((x,250,x,955),fill='#203b45',width=1)
    for y in range(255,960,70):d.line((40,y,675,y),fill='#203b45',width=1)
    mint='#a7e0c0';cream='#f4eddc'; coral='#df9277'
    if index==0:
        centered(d,'1%',322,188,fill=mint)
        centered(d,'SMALL PERCENTAGE',545,27)
        d.rounded_rectangle((108,640,610,839),28,fill='#203c48',outline='#3b6466',width=2)
        centered(d,'$10,000',669,70,fill=cream)
        centered(d,'ILLUSTRATIVE BALANCE',760,21,fill='#9caeaa')
    elif index==1:
        centered(d,'$10,000',346,102,fill=cream)
        centered(d,'BALANCE HELD CONSTANT',477,24,fill=mint)
        d.rounded_rectangle((96,575,624,785),25,fill='#203c48')
        centered(d,'× 1% per year',607,45,fill=mint)
        centered(d,'= $100',678,76,fill=coral)
        centered(d,'ANNUAL FEE IN THIS EXAMPLE',849,22)
    elif index==2:
        for y,label,value,color,amount in [(435,'1.00%',1,coral,'$100'),(669,'0.25%',.25,mint,'$25')]:
            d.text((70,y-72),label,font=font(39),fill=cream)
            d.rounded_rectangle((70,y,645,y+60),10,fill='#203c48')
            width=575*value*ease(p*3)
            if width>2:d.rounded_rectangle((70,y,70+width,y+60),10,fill=color)
            d.text((630,y-72),amount,font=font(50),fill=color,anchor='rt')
        centered(d,'$75 less / year',845,46,fill=mint)
        centered(d,'SAME CONSTANT $10,000 BALANCE',925,18)
    elif index==3:
        centered(d,'LESS IN FEES.',350,54,fill=cream)
        centered(d,'MORE STAYS INVESTED.',425,38,fill=mint)
        for i in range(8):
            h=(88+i*39)*(.3+.7*ease(p*2))
            x=77+i*73;d.rounded_rectangle((x,860-h,x+45,860),7,fill=mint if i!=7 else coral)
        centered(d,'CONCEPTUAL • NOT A RETURN FORECAST',925,18,fill='#a8b9b4')
    elif index==4:
        for j,label in enumerate(['ONGOING FEES','TRADING CHARGES','SERVICE & RISK']):
            y=374+j*175;d.rounded_rectangle((76,y,644,y+126),20,fill='#203c48',outline='#3b6466',width=2)
            d.ellipse((105,y+40,151,y+86),fill=mint)
            d.line((116,y+62,126,y+71,141,y+51),fill='#193b38',width=4)
            d.text((179,y+46),label,font=font(29),fill=cream)
        centered(d,'LOWER COST ≠ LOWER RISK',955,25,fill=coral)
    else:
        centered(d,'KNOW',350,98,fill=cream);centered(d,'WHAT YOU PAY.',475,60,fill=mint)
        d.line((185,610,535,610),fill=mint,width=3)
        centered(d,'Returns are uncertain.',683,33,style='regular')
        centered(d,'Costs are worth checking.',740,33,style='regular')
        centered(d,'Investor.gov · investor education',899,22,fill='#9cafad')
    return im


def children_scene(index,p,t):
    im=BACKGROUNDS['night' if index==5 else 'forest'].copy()
    bob=math.sin(t*4)*3; blink=(t%4.8)>4.58
    f=FOX_BLINK if blink else FOX
    if index==0:
        sprite(im,f,255+30*ease(p),947+bob,.95)
        lantern(im,461,899,1.05,.8+.2*math.sin(t*4))
    elif index==1:
        x=250+160*ease(p)
        sprite(im,f,x,947+bob,1)
        lantern(im,x+43,830+bob,.82)
        d=ImageDraw.Draw(im);d.ellipse((x+19,830+bob,x+53,851+bob),fill='#dc8242')
    elif index in (2,3):
        tree_house(im)
        sprite(im,f,262,978+bob,.85)
        if index==2:
            sprite(im,OWL_BLINK if blink else OWL,527,724,.54)
            lantern(im,309,871,.8)
        else:
            oy=744+203*ease(p)
            sprite(im,OWL,512,oy,.68)
            lantern(im,398,920,.84)
    elif index==4:
        x=250+80*p
        sprite(im,f,x,966+bob,.83)
        sprite(im,OWL,x+218,951-bob,.7)
        lantern(im,x+144,841,.88)
    else:
        d=ImageDraw.Draw(im)
        d.ellipse((87,629,638,1100),fill='#857953')
        d.ellipse((139,674,585,1055),fill='#253b39')
        d.ellipse((157,836,573,1003),fill='#b49d67')
        sprite(im,FOX_SLEEP,353,978,.8)
        lantern(im,568,939,.7)
        centered(d,'goodnight',492,44,fill='#e7dec1',style='serif')
    for i in range(13):
        x=75+(i*137%570)+math.sin(t*.7+i)*9;y=520+(i*79%395)+math.cos(t*.8+i)*11
        glow(im,x,y,12,(250,223,132),int(90+50*math.sin(t*1.5+i)))
    return im


def horror_scene(index,p,t):
    im=BACKGROUNDS['hallway'].copy();d=ImageDraw.Draw(im)
    if index==0:
        shift=math.sin(t*18)*2
        d.rounded_rectangle((68+shift,682,276+shift,1014),27,fill='#080f17',outline='#8b999d',width=4)
        d.rounded_rectangle((81+shift,699,263+shift,995),19,fill='#233f47')
        centered(d,'INCOMING CALL',744,18,x=174+shift,fill='#a7c4c8')
        centered(d,'ME',801,55,x=174+shift)
        d.ellipse((149+shift,920,198+shift,969),fill='#95b7aa')
        key(im,509,1002,.7)
    elif index==1:
        sprite(im,palm_sprite(),467,1125,.83)
        key(im,472,969,.83,angle=-17)
        glow(im,465,829,145,(166,182,151),30)
    elif index==2:
        # A restrained vibration at the letter slot follows the scraping sound.
        for i in range(3):
            o=8+i*13+math.sin(t*10)*2
            d.arc((271-o,778-o,457+o,842+o),195,345,fill='#9eaa98',width=2)
        glow(im,365,810,90,(154,183,164),int(25+15*math.sin(t*4)))
    elif index in (3,4):
        veil=Image.new('RGBA',(W,H),(2,9,15,95));im.alpha_composite(veil);d=ImageDraw.Draw(im)
        sprite(im,palm_sprite(),357,1151,1.11)
        color='#d2b67c' if index==3 and p<.3 else '#1c292e'
        key(im,362,1012,1.1,color=color,angle=-10 if index==3 else 8,tooth=(index==4 and p>.47))
        if index==4 and p>.5:
            d.line((282,925,305,946),fill='#cfbc92',width=2)
    else:
        im=gradient((5,12,19),(13,25,32));d=ImageDraw.Draw(im)
        reveal=ease(p*2.4)
        glow(im,360,620,270,(61,93,98),50)
        d=ImageDraw.Draw(im)
        d.arc((105,374,621,873),5,175,fill='#6d8883',width=6)
        d.pieslice((122,386,604,849),0,180,fill='#03090e')
        for i in range(9):
            x=164+i*48;y=668+58*math.sin(i/8*math.pi)
            key(im,x,y,.34,color='#c4b086',angle=(i-4)*-5)
        d.arc((120,373,605,850),4,175,fill='#bac0a6',width=3)
        centered(d,'A MOUTH FULL OF KEYS.',901,25,fill='#d3c6a9')
        if reveal<1:im.alpha_composite(Image.new('RGBA',(W,H),(5,12,19,int((1-reveal)*255))))
    return im


def caption(draw,text):
    lines=wrap(text,594,36)
    if len(lines)>2:
        raise ValueError('Caption overflow: '+text)
    h=56*len(lines)+30;y=1040
    draw.rounded_rectangle((45,y-10,675,y+h),18,fill=(8,19,27,235))
    for i,line in enumerate(lines): centered(draw,line,y+12+i*49,36)


def frame(channel,scene_index,p,t,caption_text):
    key_=channel['key']
    im={'wealth_blueprint':finance_scene,'children_stories':children_scene,'horror_stories':horror_scene}[key_](scene_index,p,t)
    d=ImageDraw.Draw(im)
    brands={'wealth_blueprint':'WEALTH BLUEPRINT','children_stories':'A LITTLE BEDTIME STORY','horror_stories':'ORIGINAL MIDNIGHT FICTION'}
    color={'wealth_blueprint':'#a7e0c0','children_stories':'#f7d99b','horror_stories':'#c6bda5'}[key_]
    centered(d,brands[key_],70,19,fill=color)
    heading=channel['scenes'][scene_index]['heading']
    lines=wrap(heading,606,37,'serif' if key_=='children_stories' else 'bold')
    for i,line in enumerate(lines):centered(d,line,145+i*48,37,style='serif' if key_=='children_stories' else 'bold')
    if caption_text:caption(d,caption_text)
    d.line((58,1195,662,1195),fill='#4c6261',width=2)
    d.line((58,1195,58+604*((scene_index+p)/6),1195),fill=color,width=4)
    label='ILLUSTRATIVE • EDUCATIONAL' if key_=='wealth_blueprint' else 'ORIGINAL FICTION'
    centered(d,label,1215,14,fill=color)
    return im.convert('RGB')


def srt_time(t):
    ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def prepare_audio(channel,folder):
    config=get_settings();service=KokoroLocalRuntimeService(config)
    rate=24000;parts=[];timings=[];offset=.32
    parts.append(np.zeros(round(offset*rate),dtype=np.float32))
    for i,scene in enumerate(channel['scenes']):
        path=folder/f'voice-{i+1}.wav';record=path.with_suffix('.json')
        ident=hashlib.sha256(json.dumps({'text':scene['text'],'voice':channel['voice'],'speed':channel['speed']},sort_keys=True).encode()).hexdigest()
        if record.exists():
            stored=json.loads(record.read_text(encoding='utf-8'))
            if stored['input_hash']!=ident or stored['sha256']!=sha(path):raise ValueError('Cached voice changed')
        else:
            detail=service.synthesize_to_wav(text=scene['text'],output_path=path,lang='en-us',voice_name=channel['voice'],speed=channel['speed'])
            if detail['voice_name']!=channel['voice']:raise ValueError('Unexpected substituted voice')
            write(record,{'input_hash':ident,'sha256':sha(path),'synthesis':detail})
        audio,sr=sf.read(path,dtype='float32');assert sr==rate
        active=np.flatnonzero(abs(audio)>.004)
        if not len(active):raise ValueError('Silent voice')
        audio=audio[max(0,active[0]-round(.08*rate)):min(len(audio),active[-1]+round(.12*rate))]
        duration=len(audio)/rate;gap=.36 if channel['key']=='children_stories' else .28
        if channel['key']=='horror_stories' and i==4:gap=.65
        timings.append({'index':i,'start':offset,'voice_end':offset+duration,'end':offset+duration+gap,'text':scene['text']})
        parts.extend([audio,np.zeros(round(gap*rate),dtype=np.float32)]);offset+=duration+gap
        print(channel['key'],'voice',i+1,round(duration,2),flush=True)
    voice=np.concatenate(parts);total=len(voice)/rate
    if not 30<=total<=65:raise ValueError(f'Unexpected short duration {total}')
    # Original restrained music/sound bed, synthesized locally, no external music recordings.
    axis=np.arange(len(voice))/rate;bed=np.zeros_like(voice)
    kind=channel['key']
    if kind=='children_stories':
        notes=[261.63,329.63,392.00,293.66,349.23,440.00]
        for k,at in enumerate(np.arange(.5,total,2.5)):
            local=axis-at;env=np.where(local>=0,np.exp(-np.maximum(local,0)*1.9),0)
            bed+=.010*env*(np.sin(2*np.pi*notes[k%6]*local)+.22*np.sin(4*np.pi*notes[k%6]*local))
    elif kind=='horror_stories':
        bed=.007*np.sin(2*np.pi*49*axis)+.004*np.sin(2*np.pi*73.5*axis)
        for at in (timings[0]['start']+3.4,timings[0]['start']+4.25):
            local=axis-at;env=np.clip(local/.03,0,1)*np.clip((.5-local)/.07,0,1)
            bed+=.022*env*np.sin(2*np.pi*640*local)
        rng=np.random.default_rng(42);at=timings[2]['start']+1.5;local=axis-at
        bed+=.012*rng.normal(size=len(voice))*np.clip(local/.1,0,1)*np.clip((1.1-local)/.3,0,1)
    else:
        bed=.005*np.sin(2*np.pi*130.81*axis)+.003*np.sin(2*np.pi*196*axis)
    envelope=np.minimum(1,axis/.5)*np.minimum(1,(total-axis)/.8)
    mixed=voice+bed*envelope
    if np.max(np.abs(mixed))>.99:mixed=mixed/np.max(abs(mixed))*.97
    sf.write(folder/'voice-clean.wav',voice,rate)
    sf.write(folder/'mix.wav',np.stack([mixed,mixed],axis=1),rate)
    write(folder/'timings.json',{'duration':total,'scenes':timings,'voice':channel['voice'],'speed':channel['speed'],'audio_source':'Local Kokoro plus original synthesized sound bed'})
    return timings,total


def subtitles(channel,folder,timings):
    word_path=folder/'words.json'
    if not word_path.exists():
        runtime=WhisperCppSubtitleService(get_settings().model_copy(update={'whisper_language':'en'})).runtime_availability()
        run([runtime.executable_path,'-m',runtime.model_path,'-f',str(folder/'asr.wav'),'-l','en','-ojf','-ml','1','-sow','-of',str(folder/'words')],folder/'word-alignment.log',timeout=300)
    observed=json.loads(word_path.read_text(encoding='utf-8'))['transcription']
    norm=lambda s:re.sub(r'[^a-z0-9]','',s.lower())
    observed=[r for r in observed if norm(r['text']) and r['offsets']['to']>r['offsets']['from']]
    expected_words=[w for s in timings for w in s['text'].split()]
    anchors={}
    for match in SequenceMatcher(None,[norm(w) for w in expected_words],[norm(r['text']) for r in observed],autojunk=False).get_matching_blocks():
        for k in range(match.size):
            row=observed[match.b+k]
            anchors[match.a+k]=(row['offsets']['from']/1000,row['offsets']['to']/1000)
    cues=[]
    word_offset=0
    for scene in timings:
        words=scene['text'].split();chunks=[];chunk=[]
        for word in words:
            candidate=' '.join(chunk+[word])
            if chunk and (len(wrap(candidate,594,36))>2 or len(chunk)>=7):
                chunks.append(chunk);chunk=[]
            chunk.append(word)
            if word[-1] in '.?!' and len(chunk)>=3:chunks.append(chunk);chunk=[]
        if chunk:chunks.append(chunk)
        # Captions use known spoken text, timed across the measured scene audio, not hallucinated ASR.
        weights=[sum(len(w.strip('.,!?'))**.55 for w in c)+sum(w.endswith(('.',',','?','!'))*.7 for w in c) for c in chunks]
        pos=scene['start'];duration=scene['voice_end']-scene['start']
        for c,weight in zip(chunks,weights):
            end=pos+duration*weight/sum(weights)
            start_anchor=anchors.get(word_offset,(pos,end))[0]
            end_anchor=anchors.get(word_offset+len(c)-1,(pos,end))[1]
            start=max(scene['start'],min(scene['voice_end']-.08,start_anchor))
            cue_end=min(scene['voice_end'],max(start+.08,end_anchor))
            cues.append({'start':start,'end':cue_end,'text':' '.join(c)})
            pos=end;word_offset+=len(c)
    for i,cue in enumerate(cues):
        if i:
            cue['start']=max(cue['start'],cues[i-1]['start']+.08)
            cues[i-1]['end']=min(cues[i-1]['end'],cue['start'])
        cue['end']=max(cue['end'],cue['start']+.08)
    (folder/'captions.srt').write_text('\n\n'.join(f"{i+1}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}" for i,c in enumerate(cues))+'\n',encoding='utf-8')
    write(folder/'captions.json',{'alignment':'Whisper word-timestamp anchors constrained by measured scene boundaries; unmatched words use proportional estimates. Not forced alignment.','matched_words':len(anchors),'total_words':len(expected_words),'cues':cues})
    return cues


def transcribe(folder,channel):
    config=get_settings().model_copy(update={'whisper_language':'en'})
    service=WhisperCppSubtitleService(config)
    run([FFMPEG,'-y','-v','error','-i',str(folder/'voice-clean.wav'),'-ar','16000','-ac','1',str(folder/'asr.wav')],folder/'asr-convert.log')
    out=folder/'transcript.srt'
    run(service.build_command(audio_path=folder/'asr.wav',output_path=out),folder/'transcript.log',timeout=300)
    actual=' '.join(line.strip() for line in out.read_text(encoding='utf-8').splitlines() if line.strip() and not line.strip().isdigit() and '-->' not in line)
    expected=' '.join(s['text'] for s in channel['scenes'])
    normalize=lambda s:re.findall(r"[a-z]+",s.lower())
    similarity=SequenceMatcher(None,normalize(expected),normalize(actual)).ratio()
    result={'expected':expected,'recognized':actual,'word_sequence_similarity':similarity,'status':'passed' if similarity>=.86 else 'needs_review'}
    write(folder/'transcript-check.json',result)
    if similarity<.86:raise ValueError('Voice transcription needs review: '+str(similarity))


def render(channel,folder,timings,total,cues):
    starts=[s['start'] for s in timings];cue_starts=[c['start'] for c in cues]
    output=folder/channel['filename']
    with (folder/'render.log').open('w') as log:
        proc=subprocess.Popen([FFMPEG,'-y','-v','warning','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}',
             '-r',str(FPS),'-i','pipe:0','-i',str(folder/'mix.wav'),'-vf','scale=1080:1920:flags=lanczos',
             '-af','loudnorm=I=-16:TP=-1.5:LRA=7','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p',
             '-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart','-shortest',str(output)],stdin=subprocess.PIPE,stdout=log,stderr=log)
        try:
            for n in range(math.ceil(total*FPS)):
                t=n/FPS;idx=max(0,min(5,bisect_right(starts,t)-1));scene=timings[idx]
                p=max(0,min(1,(t-scene['start'])/(scene['end']-scene['start'])))
                ci=bisect_right(cue_starts,t)-1;ct=cues[ci]['text'] if ci>=0 and t<cues[ci]['end'] else ''
                im=frame(channel,idx,p,t,ct)
                if n in [round((s['start']+min(1.2,(s['end']-s['start'])/2))*FPS) for s in timings]:im.save(folder/f'scene-{idx+1}.png')
                proc.stdin.write(im.tobytes())
                if n%(FPS*10)==0:print(channel['key'],'render',round(t,1),'/',round(total,1),flush=True)
            proc.stdin.close();code=proc.wait(timeout=120)
            if code:raise RuntimeError('FFmpeg render failed')
        except BaseException:
            proc.kill();proc.wait();raise
    poster=frame(channel,0,.5,1,'')
    poster.resize((1080,1920),Image.Resampling.LANCZOS).save(folder/'cover.png')
    probe=json.loads(subprocess.check_output([FFPROBE,'-v','error','-show_format','-show_streams','-of','json',str(output)],text=True))
    video=next(s for s in probe['streams'] if s['codec_type']=='video');audio=next(s for s in probe['streams'] if s['codec_type']=='audio')
    assert (video['width'],video['height'],video['codec_name'],audio['codec_name'])==(1080,1920,'h264','aac')
    assert abs(float(probe['format']['duration'])-total)<.2
    run([FFMPEG,'-v','error','-i',str(output),'-f','null','-'],folder/'decode-check.log',timeout=180)
    run([FFMPEG,'-hide_banner','-i',str(output),'-af','loudnorm=I=-16:TP=-1.5:LRA=7:print_format=json','-f','null','-'],folder/'loudness-check.log')
    report={'status':'rendered_and_technically_verified','title':channel['title'],'output':str(output),'sha256':sha(output),
            'duration_seconds':float(probe['format']['duration']),'width':1080,'height':1920,'video_codec':'h264','audio_codec':'aac',
            'voice':channel['voice'],'spoken_words':len(' '.join(s['text'] for s in channel['scenes']).split()),
            'production_mode':'Assisted editorial script and original deterministic 2D animation with Atnos local Kokoro/Whisper/FFmpeg. Not the full automatic fiction order pipeline.',
            'publication':False,'human_review':'not_performed','paid_api_calls':0,'sources':channel.get('sources',[]),
            'renderer_sha256':sha(Path(__file__)),
            'caption_alignment':'Whisper word-timestamp anchors inside measured scene boundaries, estimates for unmatched words'}
    write(folder/'result.json',report)
    return report


def main():
    global FOX,FOX_BLINK,FOX_SLEEP,OWL,OWL_BLINK,BACKGROUNDS
    parser=argparse.ArgumentParser();parser.add_argument('--channel',choices=['all','wealth_blueprint','children_stories','horror_stories'],default='all');parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--previews-only',action='store_true')
    args=parser.parse_args();ROOT.mkdir(parents=True,exist_ok=True);FONTS.mkdir(exist_ok=True)
    for name in ('seguisb.ttf','segoeui.ttf','georgia.ttf'):
        if not (FONTS/name).exists():shutil.copyfile(Path('C:/Windows/Fonts')/name,FONTS/name)
    FOX=fox_sprite();FOX_BLINK=fox_sprite(blink=True);FOX_SLEEP=fox_sprite(sleeping=True);OWL=owl_sprite();OWL_BLINK=owl_sprite(blink=True)
    BACKGROUNDS={'forest':forest_bg(),'night':forest_bg(True),'hallway':hallway_bg(),'finance':gradient((13,30,41),(19,46,55))}
    specification=json.loads(SPEC.read_text(encoding='utf-8'));reports=[]
    for channel in specification['channels']:
        if args.channel not in ('all',channel['key']):continue
        folder=ROOT/channel['key'];folder.mkdir(exist_ok=True)
        source=folder/'script.json'
        if source.exists() and json.loads(source.read_text(encoding='utf-8'))!=channel:raise ValueError('Changed production inputs; use a new export directory')
        write(source,channel)
        if args.previews_only:
            for i in range(6):frame(channel,i,.6,3.2,channel['scenes'][i]['text'].split('.')[0][:60]).save(folder/f'preview-{i+1}.png')
            continue
        timing=folder/'timings.json'
        if timing.exists():data=json.loads(timing.read_text(encoding='utf-8'));timings,total=data['scenes'],data['duration']
        else:timings,total=prepare_audio(channel,folder)
        if not (folder/'transcript-check.json').exists():transcribe(folder,channel)
        if json.loads((folder/'transcript-check.json').read_text(encoding='utf-8'))['status']!='passed':raise ValueError('Unresolved transcript review')
        if args.prepare_only:continue
        cues=subtitles(channel,folder,timings)
        if (folder/'result.json').exists():
            result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
            if sha(Path(result['output']))!=result['sha256']:raise ValueError('Existing output changed')
        else:result=render(channel,folder,timings,total,cues)
        reports.append(result)
        print(json.dumps(result),flush=True)
    if reports:write(ROOT/'results.json',[json.loads(p.read_text(encoding='utf-8')) for p in sorted(ROOT.glob('*/result.json'))])


if __name__=='__main__':main()
