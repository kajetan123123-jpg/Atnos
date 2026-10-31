import React from 'react';
import {interpolate,useCurrentFrame} from 'remotion';
import type {BrandSpec,ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText} from './typography';
import {compoundProgress} from './compound-series';

export function speakingAmount(scene:ManifestScene,seconds:number):number {
  const word=scene.caption_words?.find(w=>seconds>=w.start_sec&&seconds<w.end_sec);
  if(!word)return 0;
  const t=(seconds-word.start_sec)/(word.end_sec-word.start_sec);
  return Math.sin(Math.PI*t)*(.55+.45*Math.pow(Math.sin(t*Math.PI*2),2));
}

/** Preserves the actual drawn identity. Limited 2D mouth deformation, not neural lip sync. */
const Presenter:React.FC<{scene:ManifestScene;src:string;top:number}>=({scene,src,top})=>{
  const frame=useCurrentFrame();
  const amount=speakingAmount(scene,scene.timing.start_sec+frame/30);
  const map='data:image/svg+xml;charset=utf-8,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="597" height="835"><defs><linearGradient id="g" x2="0" y2="1"><stop stop-color="rgb(128,60,0)"/><stop offset="1" stop-color="rgb(128,196,0)"/></linearGradient><radialGradient id="m"><stop stop-color="white"/><stop offset=".65" stop-color="white"/><stop offset="1" stop-color="black"/></radialGradient><mask id="mask"><ellipse cx="270" cy="448" rx="66" ry="32" fill="url(#m)"/></mask></defs><rect width="597" height="835" fill="rgb(128,128,0)"/><rect x="204" y="416" width="132" height="64" fill="url(#g)" mask="url(#mask)"/></svg>');
  return <div data-wb-presenter="caption-timed-2d" style={{position:'absolute',left:72,top,width:452,height:632,borderBottom:'6px solid #00D5D8'}}>
    <svg width="452" height="632" viewBox="0 0 597 835">
      <defs>
        <filter id={'mouth-'+scene.scene_id} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
          <feImage href={map} result="mouth-map"/>
          <feDisplacementMap in="SourceGraphic" in2="mouth-map" scale={32*(1-amount)} xChannelSelector="R" yChannelSelector="G"/>
        </filter>
      </defs>
      <image href={src} width="597" height="835" filter={`url(#mouth-${scene.scene_id})`}/>
    </svg>
  </div>;
};

const Text:React.FC<{text:string;width:number;size?:number;lines?:number}>=({text,width,size=54,lines=4})=>
  <div aria-label={text}><MeasuredText text={text} width={width} maxSize={size} minSize={Math.min(36,size)} maxLines={lines}/></div>;

export const ProfessionalResearchScene:React.FC<{scene:ManifestScene;brand:BrandSpec;subjects:string[];assetData:Record<string,string>}>=({scene,brand,subjects,assetData})=>{
  const frame=useCurrentFrame();
  const host=brand.visualVariant==='professional_host';
  const src=host?assetData.wb_registered_presenter:undefined;
  if(host&&!src)throw new Error('Professional presenter variant requires registered Alex artwork');
  const relation=scene.visual_relation;
  const subject=relation?.objects?.find(o=>o.role==='subject')?.label;
  const check=relation?.objects?.find(o=>o.role==='check')?.label;
  const risk=relation?.objects?.find(o=>o.role==='risk')?.label;
  const intro=scene.scene_type==='HookScene'&&!subject;
  const chart=scene.verified_series;
  const cta=scene.scene_type==='CTAScene';
  if(!intro&&!chart&&!cta&&(!subject||!check||!risk))throw new Error('Unsupported professional research meaning');
  const riskTokens=risk?.toLowerCase().match(/[a-z]+/g)??[];
  const cue=scene.caption_words?.find(w=>riskTokens.slice(0,3).includes(w.text.toLowerCase().replace(/[^a-z]/g,''))&&w.start_sec>scene.timing.start_sec+.8);
  const riskAt=cue?(cue.start_sec-scene.timing.start_sec)*30:(scene.timing.end_sec-scene.timing.start_sec)*15;
  const reveal=(at:number)=>interpolate(frame,[at,at+10],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});
  const heading=intro?scene.narration:subject??(chart?'THE MATH BEHIND 10×':'WEALTH BLUEPRINT');
  return <SceneShell scene={scene} brand={brand} captionStyle="plain_phrase">
    <div data-wb-visual-variant={brand.visualVariant} style={{position:'absolute',inset:0}}>
      <div style={{position:'absolute',left:72,top:106,width:64,height:8,background:brand.accentSoft}}/>
      <div style={{position:'absolute',left:72,top:154,width:910,color:brand.text}}><Text text={heading} width={910} size={intro?70:chart?64:84} lines={intro?4:2}/></div>
      {host&&src?<Presenter scene={scene} src={src} top={intro?470:chart?430:360}/>:null}
      {intro?<>
        <div style={{position:'absolute',left:host?572:72,top:host?490:620,width:host?430:900}}>
          {subjects.map((name,i)=><div key={name} style={{position:'relative',boxSizing:'border-box',height:host?190:234,marginBottom:24,padding:host?'34px 28px':'48px 48px',background:brand.panel,borderLeft:`8px solid ${[brand.accentSoft,brand.accent,'#FF685B'][i]}`,opacity:reveal(i*8)}}>
            <div style={{color:brand.accentSoft,fontSize:24,letterSpacing:3,marginBottom:14}}>0{i+1}</div>
            <Text text={name} width={host?360:780} size={host?44:62} lines={2}/>
          </div>)}
        </div>
        <div style={{position:'absolute',left:72,top:host?1170:1470,width:900,color:'#91A9C4'}}><Text text="SPECULATIVE WATCHLIST · NOT A FORECAST" width={900} size={32} lines={2}/></div>
      </>:null}
      {subject&&check&&risk?<>
        <div style={{position:'absolute',left:host?572:72,top:host?360:430,width:host?430:936,height:host?632:410,boxSizing:'border-box',padding:host?32:44,background:brand.panel,borderTop:`6px solid ${brand.accentSoft}`,opacity:reveal(4)}}>
          <div style={{fontSize:27,letterSpacing:3,color:brand.accentSoft,marginBottom:36}}>RESEARCH CHECK</div>
          <Text text={check} width={host?366:848} size={host?44:60} lines={host?9:5}/>
        </div>
        <div style={{position:'absolute',left:72,top:host?1050:900,width:936,height:host?440:510,boxSizing:'border-box',padding:44,background:'#191C2C',borderTop:'6px solid #FF685B',opacity:reveal(riskAt)}}>
          <div style={{fontSize:27,letterSpacing:3,color:'#FF8073',marginBottom:32}}>WHAT CAN BREAK THE THESIS</div>
          <Text text={risk} width={848} size={host?46:54} lines={6}/>
        </div>
      </>:null}
      {chart?(()=>{
        const progress=compoundProgress(scene,frame/30);
        const left=host?572:72,top=host?395:450,width=host?430:936,height=host?560:790;
        const x=(year:number)=>72+year/chart.horizon_years*820;
        const y=(v:number)=>610-v/chart.values[3]*510;
        const points=Array.from({length:101},(_,i)=>{const yr=chart.horizon_years*progress*i/100;return `${x(yr)},${y(chart.principal*(1+chart.annual_rate)**yr)}`;});
        return <>
          <div style={{position:'absolute',left:72,top:330,color:brand.accent,fontSize:32}}>{chart.assumption_label}</div>
          <svg data-wb-verified-chart="professional" width={width} height={height} viewBox="0 0 960 720" style={{position:'absolute',left,top,background:brand.panel}}>
            {[0,.25,.5,.75,1].map(f=><line key={f} x1="72" x2="892" y1={610-f*510} y2={610-f*510} stroke="#29415C" strokeWidth="2"/>)}
            <path d="M72 70V610H900" fill="none" stroke="#91A9C4" strokeWidth="3"/>
            <polyline points={points.join(' ')} fill="none" stroke={brand.accentSoft} strokeWidth="9"/>
            {chart.years.map((yr,i)=><g key={yr} opacity={i===0||progress*chart.horizon_years>=yr?1:0}>
              <circle cx={x(yr)} cy={y(chart.values[i])} r="10" fill={brand.accent}/>
              <text x={x(yr)} y={y(chart.values[i])-26} textAnchor={i===0?'start':i===3?'end':'middle'} fill={brand.text} fontSize="46" fontWeight="900">{chart.unit==='dollars'?'$':''}{chart.display_values[i]}</text>
            </g>)}
            {chart.years.map((yr,i)=><text key={yr} x={x(yr)} y="675" textAnchor={i===0?'start':i===3?'end':'middle'} fill="#AFC2D8" fontSize="32">Year {yr}</text>)}
          </svg>
          <div style={{position:'absolute',left:72,top:host?1110:1330,width:900}}>
            <Text text="One deposit. No additional contributions." width={900} size={36} lines={2}/>
            <div style={{marginTop:26,color:'#AFC2D8'}}><Text text={chart.exclusions_label} width={900} size={32} lines={2}/></div>
          </div>
        </>;
      })():null}
      {cta?<div style={{position:'absolute',left:host?572:72,top:host?440:700,width:host?430:936,borderTop:`8px solid ${brand.accentSoft}`,paddingTop:48}}>
        <Text text={scene.narration} width={host?430:936} size={host?54:80} lines={7}/>
      </div>:null}
    </div>
  </SceneShell>;
};
