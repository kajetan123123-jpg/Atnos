import React from 'react';
import {interpolate,useCurrentFrame} from 'remotion';
import type {BrandSpec,ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText} from './typography';

/** A qualitative research sheet. No axes or invented business measurements. */
export const ResearchCheckScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=({scene,brand})=>{
  const frame=useCurrentFrame();
  const objects=scene.visual_relation?.objects??[];
  const subject=objects.find(o=>o.role==='subject')?.label;
  const check=objects.find(o=>o.role==='check')?.label;
  const risk=objects.find(o=>o.role==='risk')?.label;
  if(!subject||!check||!risk)throw new Error('Research diagram requires sourced subject, check and risk clauses');
  const tokens=risk.toLowerCase().match(/[a-z]+/g)??[];
  const cue=scene.caption_words?.find(w=>tokens.slice(0,3).includes(w.text.toLowerCase().replace(/[^a-z]/g,''))
    && w.start_sec>scene.timing.start_sec+.8);
  const riskAt=cue?Math.round((cue.start_sec-scene.timing.start_sec)*30):Math.round((scene.timing.end_sec-scene.timing.start_sec)*15);
  const reveal=(at:number)=>interpolate(frame,[at,at+14],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});
  const first=reveal(6),second=reveal(riskAt);
  const id='research-'+scene.scene_id;
  const named=subject.toLowerCase();
  const industry=named==='rocket lab'?'space':named==='sofi'?'banking':['hims','hims and hers'].includes(named)?'health':'unmapped';
  return <SceneShell scene={scene} brand={brand} showBrandMark={false} compactCaptions>
    <svg width="1080" height="1920" viewBox="0 0 1080 1920" style={{position:'absolute',inset:0}}>
      <defs><linearGradient id={id} x2=".8" y2="1"><stop stopColor="#FFF9E9"/><stop offset="1" stopColor="#E5D0AA"/></linearGradient></defs>
      <path d="M-20 350 Q410 256 1110 404 V1920 H0Z" fill="#17586C"/>
      <path d="M0 510 Q362 437 1080 557 M0 1720 Q491 1600 1080 1705" stroke="#75B6AA" strokeWidth="32" opacity=".13" fill="none"/>
      <path d="M90 413 L998 438 L985 1510 L78 1480Z" fill="#091C38" opacity=".5"/>
      <path d="M72 392 Q520 369 986 411 L969 1488 Q527 1455 65 1471Z" fill={`url(#${id})`}/>
      <path d="M85 425 Q516 409 956 435 M85 1439 Q499 1418 952 1451" stroke="#B89B6B" strokeWidth="5" opacity=".35" fill="none"/>
      <path d="M105 491 Q421 478 879 500" stroke="#D3B478" strokeWidth="19" opacity=".3" fill="none"/>
      <path d="M122 815 Q443 831 903 809" stroke="#187F83" strokeWidth="7" strokeLinecap="round" pathLength={1} strokeDasharray={1} strokeDashoffset={1-first} fill="none"/>
      <path d="M122 1260 Q495 1279 903 1253" stroke="#D16851" strokeWidth="7" strokeLinecap="round" pathLength={1} strokeDasharray={1} strokeDashoffset={1-second} fill="none"/>
      <path d="M127 942 Q161 925 192 944" stroke="#D16851" strokeWidth="12" opacity={second} fill="none"/>
      <g data-wb-research-subject={industry} transform="translate(754 126) scale(.85)" strokeLinecap="round" strokeLinejoin="round">
        <ellipse cx="140" cy="251" rx="136" ry="22" fill="#09112C" opacity=".45"/>
        {industry==='space'?<>
          <path d="M40 250 H247 M57 242 V70 H90 M60 116 H100 M60 165 H103 M61 211 H108" fill="none" stroke="#537C85" strokeWidth="9"/>
          <path d="M112 230 L110 100 Q127 45 152 22 Q180 52 191 102 L194 230Z" fill="#F0DFC0" stroke="#131B37" strokeWidth="7"/>
          <path d="M115 102 Q129 58 153 30 L157 228 H118Z" fill="#A4B9AD" opacity=".65"/>
          <path d="M113 172 Q83 189 87 237 L111 224 M193 173 Q222 189 220 239 L194 224" fill="#C3914E" stroke="#131B37" strokeWidth="6"/>
          <path d="M120 214 H187 L182 238 H125Z" fill="#263F4F"/>
          <path d="M139 82 L145 69 M127 130 L128 187 M169 112 L171 165" fill="none" stroke="#FFF3D5" strokeWidth="5" opacity=".6"/>
        </>:industry==='banking'?<>
          <path d="M35 94 L190 58 L244 90 L91 127Z" fill="#CBA66A" stroke="#151A36" strokeWidth="6"/>
          <path d="M35 94 L36 226 L91 257 V127Z" fill="#1A6E79" stroke="#151A36" strokeWidth="6"/>
          <path d="M91 127 L244 90 L242 219 L91 257Z" fill="#EAD9B7" stroke="#151A36" strokeWidth="6"/>
          <path d="M115 151 L216 127 M115 177 L200 157 M115 204 L208 181" fill="none" stroke="#7A8B83" strokeWidth="8"/>
          <path d="M25 186 L89 174 L111 224 L46 240Z" fill="#CD9F62" stroke="#151A36" strokeWidth="6"/>
          <path d="M45 201 L73 195 M54 215 L82 209" fill="none" stroke="#FFF0CF" strokeWidth="5"/>
        </>:industry==='health'?<>
          <path d="M23 156 L143 124 L251 162 L131 200Z" fill="#CBA46C" stroke="#151A36" strokeWidth="6"/>
          <path d="M23 156 L24 228 L131 269 V200Z" fill="#947A59" stroke="#151A36" strokeWidth="6"/>
          <path d="M131 200 L251 162 L250 233 L131 269Z" fill="#E1C79E" stroke="#151A36" strokeWidth="6"/>
          <path d="M95 99 Q79 101 80 125 L83 187 Q115 210 152 188 L156 125 Q154 101 137 99Z" fill="#EAE1C7" stroke="#151A36" strokeWidth="6"/>
          <path d="M92 66 Q115 55 144 66 L145 100 Q120 111 92 100Z" fill="#276D78" stroke="#151A36" strokeWidth="6"/>
          <path d="M86 135 Q116 148 151 134 L150 174 Q117 186 84 173Z" fill="#7CA99A"/>
          <path d="M98 116 L98 128 M98 151 L98 168 M103 73 L103 91 M117 72 L117 93 M132 73 L132 91" fill="none" stroke="#FFF4D9" strokeWidth="4" opacity=".6"/>
        </>:null}
      </g>
    </svg>
    <div style={{position:'absolute',left:76,top:144,width:640,color:'#FFF2D5'}}><MeasuredText text={subject} width={640} maxSize={68} minSize={48} maxLines={2}/></div>
    <div style={{position:'absolute',left:120,top:455,width:790,color:'#171A3B',opacity:first}}>
      <div style={{fontSize:28,letterSpacing:4,color:'#187F83',marginBottom:32}}>WHAT TO CHECK</div>
      <MeasuredText text={check} width={790} maxSize={50} minSize={38} maxLines={5} weight={700}/>
    </div>
    <div style={{position:'absolute',left:120,top:942,width:790,color:'#171A3B',opacity:second}}>
      <div style={{fontSize:28,letterSpacing:4,color:'#AB4C3D',marginBottom:32}}>WHAT CAN BREAK THE THESIS</div>
      <MeasuredText text={risk} width={790} maxSize={50} minSize={38} maxLines={5} weight={700}/>
    </div>
  </SceneShell>;
};
