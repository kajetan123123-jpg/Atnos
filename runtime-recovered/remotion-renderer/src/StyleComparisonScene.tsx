import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import type {BrandSpec, ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText} from './typography';
import {WBPlanningDeskScene, supportsPlanningDesk} from './WBPlanningDeskScene';

/** Original WB compositions, inspired by craft principles, never copied footage.
 * The decision graph is qualitative: no invented allocation, amounts or growth.
 */
export const comparisonSupported = (s:ManifestScene) => s.scene_type==='CTAScene' || (
  s.visual_relation?.kind==='reserve_readiness' && ['identify','separate','review'].includes(s.visual_relation.stage??''));
export const comparisonCue = (s:ManifestScene, tokens:string[], fallback:number) => {
  const word=s.caption_words?.find(w=>tokens.includes(w.text.toLowerCase().replace(/[^a-z]/g,'')));
  return word?Math.max(0,(word.start_sec-s.timing.start_sec)*30):fallback;
};
const reveal=(frame:number,at:number)=>interpolate(frame,[at,at+22],[0,1],{
  extrapolateLeft:'clamp',extrapolateRight:'clamp',easing:t=>t*t*(3-2*t)});
const Text:React.FC<{text:string;width:number;size?:number;serif?:boolean;lines?:number}>=({text,width,size=66,serif=false,lines=3})=><div style={{fontFamily:serif?'Georgia, Times New Roman, serif':undefined}}>
  <MeasuredText text={text} width={width} maxSize={size} minSize={size-8} maxLines={lines} weight={serif?700:900} fontFamily={serif?'Georgia, "Times New Roman", serif':'Atnos'}/></div>;

const Folder:React.FC<{x:number;y:number;color:string;kind:'reserve'|'goal';ink:string}>=({x,y,color,kind,ink})=><g transform={`translate(${x} ${y})`}>
  <path d="M9 64 L9 30 Q9 8 30 8 H112 L141 37 H267 Q287 37 287 59 V211 Q287 230 268 230 H28 Q9 230 9 211Z" fill={ink} opacity=".25" transform="translate(8 15)"/>
  <path d="M0 58 V23 Q0 0 23 0 H105 L133 29 H258 Q282 29 282 53 V208 Q282 226 260 226 H22 Q0 226 0 205Z" fill={color}/>
  <path d="M22 71 L250 60 V209 H22Z" fill="#FFF3D9"/>
  <path d="M0 96 Q145 75 282 87 V208 Q282 226 260 226 H22 Q0 226 0 205Z" fill={color}/>
  {kind==='reserve'?<><path d="M144 112 L186 129 V161 Q181 187 144 201 Q108 186 103 161 V129Z" fill="none" stroke={ink} strokeWidth="8"/><path d="M125 153 L140 168 L166 140" fill="none" stroke={ink} strokeWidth="7"/></>:
    <><path d="M115 196 V112 M116 113 Q155 94 184 118 L167 144 Q143 129 116 144" fill="none" stroke={ink} strokeWidth="8"/><path d="M92 199 H190" stroke={ink} strokeWidth="6"/></>}
</g>;

export const StyleComparisonScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=({scene,brand})=>{
  if(!comparisonSupported(scene)) throw new Error('Style comparison requires supported qualitative reserve meaning');
  const frame=useCurrentFrame(), style=brand.comparisonStyle;
  const cta=scene.scene_type==='CTAScene', review=scene.visual_relation?.stage==='review';
  const before=scene.visual_relation?.stage==='identify';
  const a=review?1:reveal(frame,comparisonCue(scene,['emergency','safety','two'],24));
  const b=review?1:reveal(frame,comparisonCue(scene,['future','planned','both'],54));
  const check=reveal(frame,comparisonCue(scene,['committed','overlap','doublecounting'],100));
  const headline=scene.headline || 'One balance. Two purposes.';
  if(style==='painted_editorial' && !cta && supportsPlanningDesk(scene))
    return <WBPlanningDeskScene scene={scene} brand={{...brand,illustrationSystem:'wb_planning_desk_painted_v1',presentation:'educational_illustration'}}/>;
  const editorial=style==='editorial_magazine', pigment=style==='pigment_motion';
  const ink=editorial?'#152C36':pigment?'#23284A':'#FFF2D5';
  const paper=editorial?'#EFE5D1':pigment?'#F4EBDD':'#171A3B';
  const gold=editorial?'#AF6D37':'#F6BE4F', teal=pigment?'#448FB9':'#36C6BE';
  const id='style-'+scene.scene_id;
  const note=review?(frame<comparisonCue(scene,['overlap','doublecounting'],240)?'Check existing commitments.':
    frame<comparisonCue(scene,['confusing'],420)?'Count each portion once.':'Keep the two purposes distinct.'):
    before?'One balance. Two purposes.':'Name the two jobs.';
  return <SceneShell scene={scene} brand={{...brand,background:paper,text:ink}} captionStyle="plain_phrase" compactCaptions>
    <div data-wb-comparison-style={style} style={{position:'absolute',inset:0,color:ink}}>
      <svg width="1080" height="1920" style={{position:'absolute',inset:0}} aria-label="Qualitative decision diagram, not a numerical allocation">
        <defs>
          <filter id={id+'-grain'} x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".12" numOctaves="2" seed="17"/><feColorMatrix type="saturate" values="0"/></filter>
          <linearGradient id={id+'-wash'} x2=".8" y2="1"><stop stopColor="#FFD569"/><stop offset=".5" stopColor="#F3A3A0"/><stop offset="1" stopColor="#788DCE"/></linearGradient>
        </defs>
        {pigment?<><ellipse cx="850" cy="420" rx="460" ry="570" fill={'url(#'+id+'-wash)'} opacity=".45"/><path d="M-40 1020 Q520 880 1120 1160 L1100 1460 Q470 1160 -40 1410Z" fill="#70BDB3" opacity=".18"/></>:
          editorial?<><path d="M72 180 H1008 M72 1442 H1008" stroke={ink} strokeWidth="2"/><path d="M988 220 V1390" stroke={ink} strokeWidth="1" opacity=".3"/></>:
          <><path d="M0 700 Q320 530 1080 680 V1920 H0Z" fill="#252354"/><path d="M0 1350 Q680 1140 1080 1330 V1920 H0Z" fill="#303765"/><circle cx="975" cy="286" r="110" fill="#36C6BE" opacity=".12"/></>}
        {pigment?<rect x="0" y="0" width="1080" height="1560" filter={'url(#'+id+'-grain)'} style={{mixBlendMode:'multiply'}} opacity=".045"/>:null}
      </svg>
      <div style={{position:'absolute',left:78,top:112,fontSize:23,letterSpacing:4,fontWeight:700}}>WEALTH BLUEPRINT</div>
      <div style={{position:'absolute',left:78,top:editorial?230:208,width:880}}>
        <Text text={cta?'Build wealth on purpose.':headline} width={880} size={editorial?83:78} serif={editorial||pigment} lines={3}/>
      </div>
      {cta?<>
        <div style={{position:'absolute',left:editorial?78:120,top:650,width:850}}>
          <svg width="840" height="430" viewBox="0 0 840 430"><Folder x={75} y={95} color={gold} kind="reserve" ink="#253647"/><Folder x={460} y={95} color={teal} kind="goal" ink="#253647"/><path d="M387 66 V365" stroke={ink} strokeWidth="3" opacity=".5"/></svg>
          <div style={{fontSize:32,marginTop:30,letterSpacing:2}}>CLEAR ROLES. DELIBERATE CHOICES.</div>
        </div>
        <div style={{position:'absolute',left:78,top:1320,fontSize:38,color:editorial?gold:ink}}>Follow Wealth Blueprint</div>
      </>:editorial?<>
        <div style={{position:'absolute',left:78,top:580,fontSize:26,letterSpacing:3,color:gold}}>YOUR MONEY / ITS PURPOSE</div>
        <svg width="1080" height="1920" style={{position:'absolute',inset:0}}>
          <path d="M78 670 H928 V1230 H78Z" fill="#F8F2E4" stroke={ink} strokeWidth="3"/>
          <path d="M78 748 H928 M78 955 H928 M504 748 V1230" stroke={ink} strokeWidth="2"/>
          <path d="M100 705 H340 M100 718 H250" stroke={ink} strokeWidth="3" opacity=".18"/>
          <Folder x={144} y={850} color="#C5BAA2" kind="reserve" ink="#213342"/>
          <g opacity={b}><Folder x={566} y={850} color="#C38B5A" kind="goal" ink="#213342"/></g>
          {review?<path d="M132 1164 Q320 1191 444 1154 M564 1164 Q744 1194 893 1154" stroke={gold} strokeWidth="9" fill="none" pathLength="1" strokeDasharray="1" strokeDashoffset={1-check}/>:null}
        </svg>
        <div style={{position:'absolute',left:110,top:690,fontSize:34,fontWeight:700}}>ONE CURRENT BALANCE</div>
        <div style={{position:'absolute',left:112,top:785,width:350}}><Text text="Emergency reserve" width={350} size={38} serif lines={2}/></div>
        <div style={{position:'absolute',left:548,top:785,width:350,opacity:b}}><Text text="Future purchase" width={350} size={38} serif lines={2}/></div>
        <div style={{position:'absolute',left:78,top:1290,width:880}}><Text text={note} width={880} size={48} serif/></div>
      </>:<>
        <svg width="1080" height="1920" style={{position:'absolute',inset:0}}>
          <g transform={pigment?'translate(0 -30)':'translate(0 0)'}>
            <path d={pigment?'M216 550 Q560 516 862 570 L838 824 Q518 860 229 800Z':'M253 547 Q253 520 282 520 H798 Q827 520 827 550 V800 Q827 829 798 829 H282 Q253 829 253 800Z'} fill={pigment?'url(#'+id+'-wash)':'#333565'} stroke={pigment?'#293449':'#606D8E'} strokeWidth="3"/>
            <path d="M286 608 H568 M286 635 H464 M286 747 H745" stroke={ink} strokeWidth="5" opacity=".2"/>
            <path d="M540 829 V891 M540 891 H274 V1008 M540 891 H806 V1008" fill="none" stroke={pigment?'#4D667E':gold} strokeWidth="5" pathLength="1" strokeDasharray="1" strokeDashoffset={1-Math.min(a,b)}/>
            <g opacity={a} transform={`translate(0 ${12*(1-a)})`}><Folder x={130} y={1030} color={gold} kind="reserve" ink="#26334A"/></g>
            <g opacity={b} transform={`translate(0 ${12*(1-b)})`}><Folder x={668} y={1030} color={teal} kind="goal" ink="#26334A"/></g>
            {review?<path d="M110 1350 Q279 1380 434 1342 M646 1350 Q800 1383 969 1342" stroke={pigment?'#A85E41':'#ED735C'} strokeWidth="8" fill="none" pathLength="1" strokeDasharray="1" strokeDashoffset={1-check}/>:null}
          </g>
        </svg>
        <div style={{position:'absolute',left:304,top:pigment?628:658,width:480}}><Text text="One balance" width={480} size={pigment?73:65} serif={pigment} lines={1}/></div>
        <div style={{position:'absolute',left:104,top:pigment?1270:1300,width:380,opacity:a}}><Text text="Emergency reserve" width={380} size={36} serif={pigment} lines={2}/></div>
        <div style={{position:'absolute',left:640,top:pigment?1270:1300,width:350,opacity:b}}><Text text="Future purchase" width={350} size={36} serif={pigment} lines={2}/></div>
        <div style={{position:'absolute',left:78,top:1450,width:890}}><Text text={note} width={890} size={40} serif={pigment}/></div>
      </>}
    </div>
  </SceneShell>;
};
