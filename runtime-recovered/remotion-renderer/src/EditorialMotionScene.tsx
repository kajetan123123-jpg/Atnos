import React from 'react';
import {useCurrentFrame} from 'remotion';
import type {BrandSpec,ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText} from './typography';
import {compoundProgress} from './compound-series';
import {editorialDirection,editorialState,EDITORIAL_COLORS as C} from './editorial-motion';
import {EditorialPlanningScene} from './EditorialPlanningScene';

const Copy:React.FC<{text:string;x:number;y:number;width?:number;size?:number;lines?:number;color?:string;weight?:number}>=
 ({text,x,y,width=860,size=48,lines=3,color=C.paper,weight=900})=>
 <div style={{position:'absolute',left:x,top:y,color}}><MeasuredText text={text} width={width} maxSize={size} minSize={size-4} maxLines={lines} weight={weight}/></div>;

/** Full-frame editorial shots. Every diagram is labelled as a research question,
 * not a measured company outcome. Numeric plotting is restricted to verified_series. */
export const EditorialMotionScene:React.FC<{scene:ManifestScene;brand:BrandSpec;subjects:string[]}>=({scene,brand,subjects})=>{
 const frame=useCurrentFrame();
 if(scene.visual_relation?.kind==='reserve_readiness')return <EditorialPlanningScene scene={scene} brand={brand}/>;
 const t=frame/30,d=editorialDirection(scene),s=editorialState(scene,t);
 const id='edit-'+scene.scene_id;
 const research=['execution_to_cash','per_share_tradeoff','repeat_business'].includes(d.mode);
 return <SceneShell scene={scene} brand={{...brand,captionStyle:'plain_phrase'}} showBrandMark={false} compactCaptions>
  <div data-editorial-mode={d.mode} data-editorial-shot={s.shot} style={{position:'absolute',inset:0,
    background:'radial-gradient(ellipse at 15% 45%, #193945 0%, transparent 65%), linear-gradient(150deg,#0C1B2B,#07111E 70%)'}}>
   <svg width="1080" height="1920" style={{position:'absolute',inset:0}} aria-hidden>
    <defs>
     <linearGradient id={id+'metal'}><stop stopColor="#15394D"/><stop offset=".45" stopColor="#BCDFD8"/><stop offset=".57" stopColor="#4C8791"/><stop offset="1" stopColor="#123043"/></linearGradient>
     <linearGradient id={id+'gold'} x2="1" y2="1"><stop stopColor="#F6DA9C"/><stop offset="1" stopColor="#B57E33"/></linearGradient>
     <linearGradient id={id+'area'} x2="0" y2="1"><stop stopColor={C.teal} stopOpacity=".22"/><stop offset="1" stopColor={C.teal} stopOpacity="0"/></linearGradient>
     <marker id={id+'arrow'} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10" fill={C.teal}/></marker>
    </defs>
    {Array.from({length:14},(_,i)=><line key={i} x1="0" x2="1080" y1={180+i*100} y2={180+i*100} stroke="#8AA6B7" strokeWidth="1" opacity=".035"/>)}
   </svg>

   {d.mode==='question'?<>
    <Copy text="What would it take?" x={80} y={230} size={74} lines={2}/>
    <svg width="1080" height="1920" style={{position:'absolute',inset:0}}>
     <text x="56" y="850" fontSize="470" fontWeight="900" fill="none" stroke={C.teal} strokeWidth="2" opacity=".16">10×</text>
     <g transform={`translate(0 ${32*(1-s.entrance)})`} opacity={s.entrance}>
      <text x="86" y="810" fontSize="350" fontWeight="900" fill={C.paper}>10×</text>
      <text x="825" y="810" fontSize="160" fontWeight="700" fill={C.gold}>?</text>
     </g>
     <path d="M84 932H940" stroke={C.gold} strokeWidth="2"/>
     {subjects.map((name,i)=><g key={name} opacity={easeReveal(t,.45+i*.19)}>
      <text x="84" y={1052+i*122} fill={C.gold} fontSize="28">0{i+1}</text>
      <text x="158" y={1052+i*122} fill={C.paper} fontSize="51" fontWeight="700">{name}</text>
      <path d={`M820 ${1035+i*122}H930m-14-10 14 10-14 10`} stroke={C.muted} fill="none" strokeWidth="2"/>
     </g>)}
    </svg>
    <Copy text="SPECULATIVE · NOT A FORECAST" x={84} y={1400} size={27} color={C.gold} lines={1}/>
   </>:null}

   {research?<>
    <Copy text={d.subject!} x={80} y={205} size={83} lines={2}/>
    <Copy text={d.check!} x={84} y={345} size={42} lines={3} weight={700}/>
    <svg data-editorial-diagram={d.mode} width="1080" height="1920" style={{position:'absolute',inset:0}}>
     <g transform={`translate(540 860) scale(${s.cameraScale}) translate(${-s.cameraX} -860)`}>
     {d.mode==='execution_to_cash'?<>
      <g transform={`translate(284 ${676-34*s.build})`}>
       <ellipse cx="0" cy="398" rx="150" ry="22" fill="#030C17" opacity=".65"/>
       <path d="M-52 310V40Q-52-60 0-128Q52-60 52 40V310Z" fill={'url(#'+id+'metal)'} stroke="#80AEB5" strokeWidth="2"/>
       <path d="M-52 230L-103 324L-51 296M52 230L103 324L51 296" fill="#285265" stroke="#7DB7BF" strokeWidth="2"/>
       <path d="M-38 310L-30 337H30L38 310" fill="#101E2D" stroke={C.muted}/>
       <path d="M-43 60H43M-50 220H50M0-112V320" stroke={C.paper} opacity=".4" strokeWidth="2" strokeDasharray="9 9"/>
       <path d="M-110-105V337M-125-105H-95M-125 337H-95" stroke={C.muted} strokeWidth="2"/>
       <text transform="translate(-135 260) rotate(-90)" fontSize="24" fill={C.muted} letterSpacing="5">EXECUTION CHECK</text>
      </g>
      <path d="M378 734H584V686H896" pathLength="1" strokeDasharray="1" strokeDashoffset={1-s.build} stroke={C.teal} strokeWidth="4" fill="none" markerEnd={'url(#'+id+'arrow)'}/>
      <text x="587" y="638" opacity={s.build} fill={C.teal} fontSize="39" fontWeight="700">CONTRACTS</text>
      <path d="M742 724V958" pathLength="1" strokeDasharray="1" strokeDashoffset={1-s.build} stroke={C.teal} strokeWidth="4" markerEnd={'url(#'+id+'arrow)'}/>
      <text x="665" y="1036" opacity={s.build} fill={C.paper} fontSize="65" fontWeight="900">CASH?</text>
      <g opacity={s.risk}><path d="M706 827H778" stroke={C.risk} strokeWidth="12"/><circle cx="742" cy="827" r="45" fill={C.ink} stroke={C.risk} strokeWidth="3"/><text x="731" y="842" fill={C.risk} fontSize="45">!</text></g>
     </>:null}
     {d.mode==='per_share_tradeoff'?<>
      <path d="M120 700C320 700 320 830 526 830" pathLength="1" strokeDasharray="1" strokeDashoffset={1-s.build} stroke={C.teal} strokeWidth="5" fill="none" markerEnd={'url(#'+id+'arrow)'}/>
      <path d="M120 1035C320 1035 320 895 526 895" pathLength="1" strokeDasharray="1" strokeDashoffset={1-s.build} stroke={C.risk} strokeWidth="4" fill="none"/>
      <text x="110" y="646" fill={C.teal} fontSize="39" fontWeight="700">MEMBER VALUE</text>
      <text x="110" y="1103" fill={C.risk} fontSize="39" fontWeight="700">LOAN LOSSES</text>
      <ellipse cx="695" cy="862" rx="153" ry="175" fill="#173344" stroke={C.muted} strokeWidth="2"/>
      <path d="M548 862H842" stroke={C.gold} strokeWidth="3"/>
      <text x="695" y="820" textAnchor="middle" fill={C.paper} fontSize="46" fontWeight="900">BUSINESS</text>
      <text x="695" y="925" textAnchor="middle" fill={C.paper} fontSize="46" fontWeight="900">PER SHARE?</text>
      <g opacity={s.risk}><path d="M860 733V1017M844 733H877M844 1017H877" stroke={C.gold} strokeWidth="3"/><text transform="translate(922 1025) rotate(-90)" fontSize="29" fill={C.gold} letterSpacing="4">VALUATION / DILUTION</text></g>
     </>:null}
     {d.mode==='repeat_business'?<>
      <circle cx="525" cy="830" r="212" fill="#102C3A" stroke="#345460" strokeWidth="2"/>
      <path d="M384 678A207 207 0 1 1 329 894" pathLength="1" strokeDasharray="1" strokeDashoffset={1-s.build} stroke={C.teal} strokeWidth="10" fill="none" markerEnd={'url(#'+id+'arrow)'}/>
      <text x="525" y="812" textAnchor="middle" fill={C.paper} fontSize="52" fontWeight="900">REPEAT</text>
      <text x="525" y="882" textAnchor="middle" fill={C.paper} fontSize="52" fontWeight="900">CUSTOMERS</text>
      <path d="M430 1055V1124H818V917" stroke={C.gold} strokeWidth="3" fill="none"/>
      <text x="472" y="1174" fill={C.gold} fontSize="34">MARGIN CHECK</text>
      <g opacity={s.risk}><path d="M782 925L818 873L854 925Z" fill={C.ink} stroke={C.risk} strokeWidth="3"/><text x="808" y="917" fill={C.risk} fontSize="31">!</text></g>
     </>:null}
     </g>
    </svg>
    <div style={{position:'absolute',left:84,top:1230,width:856,opacity:s.risk}}>
     <div style={{height:3,width:68,background:C.risk,marginBottom:18}}/>
     <div style={{fontSize:23,letterSpacing:3,color:C.risk,marginBottom:16}}>WHAT COULD BREAK THE THESIS</div>
     <MeasuredText text={d.risk!} width={856} maxSize={39} minSize={35} maxLines={4} weight={700}/>
    </div>
    <Copy text="RESEARCH SCHEMATIC · NOT COMPANY DATA" x={84} y={1490} size={22} lines={1} color={C.muted}/>
   </>:null}

   {d.mode==='verified_curve'&&scene.verified_series?(()=>{
    const v=scene.verified_series,p=compoundProgress(scene,t),max=v.values[3];
    const x=(year:number)=>116+year/v.horizon_years*790,y=(value:number)=>1150-value/max*610;
    const coords=Array.from({length:121},(_,i)=>{const yr=v.horizon_years*p*i/120;return [x(yr),y(v.principal*(1+v.annual_rate)**yr)];});
    const line='M'+coords.map(pt=>pt.join(',')).join('L');
    return <>
     <Copy text="10× needs a high hurdle." x={80} y={210} size={70} lines={2}/>
     <Copy text={v.assumption_label} x={84} y={410} size={33} color={C.gold} lines={1}/>
     <svg width="1080" height="1920" style={{position:'absolute',inset:0}} data-verified-curve="editorial">
      {[0,.25,.5,.75,1].map(f=><g key={f}><line x1="116" x2="906" y1={1150-f*610} y2={1150-f*610} stroke="#526A76" opacity=".3"/><text x="96" y={1160-f*610} textAnchor="end" fill={C.muted} fontSize="26">{Math.round(max*f)}</text></g>)}
      <path d={line+`L${coords[coords.length-1][0]},1150L116,1150Z`} fill={'url(#'+id+'area)'}/>
      <path d={line} fill="none" stroke={C.teal} strokeWidth="7"/>
      <text x="84" y="500" fill={C.muted} fontSize="28">{v.unit==='dollars'?'USD':v.unit}</text>
      {v.years.map((yr,i)=><g key={yr}>
       <text x={x(yr)} y="1218" textAnchor={i===0?'start':i===3?'end':'middle'} fill={C.muted} fontSize="28">Year {yr}</text>
       <g opacity={p*v.horizon_years>=yr?1:0}><circle cx={x(yr)} cy={y(v.values[i])} r="8" fill={C.gold}/><text x={x(yr)} y={y(v.values[i])-30} textAnchor={i===0?'start':i===3?'end':'middle'} fill={C.paper} fontSize="43" fontWeight="900">{v.unit==='dollars'?'$':''}{v.display_values[i]}</text></g>
      </g>)}
     </svg>
     <Copy text="One deposit · No additional contributions" x={84} y={1300} size={31} lines={2}/>
     <Copy text={v.exclusions_label} x={84} y={1380} size={30} color={C.muted} lines={2}/>
    </>;
   })():null}

   {d.mode==='closing'?<>
    <svg width="1080" height="1920" style={{position:'absolute',inset:0}}><path d="M84 638H940" stroke={C.gold} strokeWidth="3"/><text x="80" y="530" fill="none" stroke={C.teal} strokeWidth="1.5" fontSize="175" fontWeight="900">PURPOSE.</text></svg>
    <Copy text={scene.narration} x={84} y={730} size={80} lines={5}/>
   </>:null}
  </div>
 </SceneShell>;
};
function easeReveal(t:number,at:number){return Math.min(1,Math.max(0,(t-at)/.35));}
