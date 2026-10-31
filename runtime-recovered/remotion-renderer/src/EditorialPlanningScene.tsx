import React from 'react';
import {useCurrentFrame} from 'remotion';
import type {BrandSpec,ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText} from './typography';
import {ease,EDITORIAL_COLORS as C} from './editorial-motion';
import {planningDeskAction} from './WBPlanningDeskScene';

const token=(value:string)=>value.toLowerCase().replace(/[^a-z]/g,'');

/** A qualitative claims diagram, not a measured allocation or price chart. */
export function editorialClaimsDirection(scene:ManifestScene){
  const relation=scene.visual_relation,words=scene.caption_words??[];
  if(relation?.kind!=='reserve_readiness' || !['identify','review'].includes(relation.stage??'')
    || relation.planning_desk_action!=='shared-balance' || planningDeskAction(scene)!=='shared-balance'
    || !words.length)throw new Error('Editorial claims requires a supported audio-bound shared-balance plan');
  if(!relation.objects?.some(o=>o.role==='shared_portion')
    || !relation.objects.some(o=>o.role==='reserve') || !relation.objects.some(o=>o.role==='goal')
    || relation.objects.some(o=>o.role==='expense' || o.label==='PLANNED BILL FUND'))
    throw new Error('Editorial claims objects must describe one portion and two purposes, not an expense');
  if(/\bemergency\b/i.test(scene.narration) && /\b(?:reduces?|less|decreases?)\b/i.test(scene.narration))
    throw new Error('Conditional emergency consequence is not supported by the claims-only composition');
  if(/[\d$€£%]/.test(scene.narration))throw new Error('Editorial claims is qualitative; numeric claims need their data composition');
  const duration=scene.timing.end_sec-scene.timing.start_sec;
  let end=scene.timing.start_sec;
  for(const word of words){
    if(!Number.isFinite(word.start_sec)||!Number.isFinite(word.end_sec)||word.end_sec<=word.start_sec
      ||word.start_sec<scene.timing.start_sec-.001||word.start_sec<end-.001||word.end_sec>scene.timing.end_sec+.001)
      throw new Error('Editorial claims requires monotonic final-voice word times');
    end=word.end_sec;
  }
  const find=(tokens:string[],after=-Infinity)=>words.find(w=>tokens.includes(token(w.text))&&w.start_sec>after);
  const first=find(['promised','assigned','counted','same','overlap']);
  const second=first&&find(['twice','both','two'],first.start_sec);
  if(!first||!second)throw new Error('Editorial claims needs both actual spoken claim cues');
  const firstAt=first.start_sec-scene.timing.start_sec,secondAt=second.start_sec-scene.timing.start_sec;
  if(secondAt+.6+.8>duration)throw new Error('Editorial claims needs a reading hold after the second claim');
  const savings=find(['savings','funds','money','portion']);
  const spent=/\bnot\s+spent\b/i.test(scene.narration)?find(['spent']):undefined;
  return {firstAt,secondAt,savingsAt:(savings?.start_sec??words[0].start_sec)-scene.timing.start_sec,
    spentAt:spent?spent.start_sec-scene.timing.start_sec:null,duration};
}

export function editorialClaimsState(scene:ManifestScene,frame:number){
  const d=editorialClaimsDirection(scene),t=frame/30;
  const reveal=(at:number,seconds=.6)=>ease((t-at)/seconds);
  return {entrance:reveal(0,.35),portion:reveal(d.savingsAt,.4),
    notSpent:d.spentAt===null?0:reveal(d.spentAt,.3),
    firstClaim:reveal(d.firstAt),secondClaim:reveal(d.secondAt),
    conflict:reveal(d.secondAt+.4,.2),hold:t>=d.secondAt+.6};
}

const Label:React.FC<{text:string;x:number;y:number;width:number;size:number;lines?:number;color?:string;opacity?:number}>=
  ({text,x,y,width,size,lines=2,color=C.paper,opacity=1})=><div style={{position:'absolute',left:x,top:y,color,opacity}}>
    <MeasuredText text={text} width={width} maxSize={size} minSize={size-4} maxLines={lines} weight={900}/>
  </div>;

const ClaimsScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=({scene,brand})=>{
  const frame=useCurrentFrame(),s=editorialClaimsState(scene,frame);
  const lightId='claims-light-'+scene.scene_id.replace(/[^\w-]/g,'');
  return <SceneShell scene={scene} brand={{...brand,captionStyle:'plain_phrase'}} showBrandMark={false} compactCaptions>
    <div data-editorial-planning="shared-balance" data-editorial-reading-hold={s.hold}
      style={{position:'absolute',inset:0,background:C.ink}}>
      <svg width="1080" height="1920" style={{position:'absolute',inset:0}} aria-label="One unchanged portion of savings claimed by two purposes">
        <defs><linearGradient id={lightId} x2="0" y2="1"><stop stopColor="#183241"/><stop offset="1" stopColor={C.ink}/></linearGradient></defs>
        <path d="M0 0H1080V1920H0Z" fill={'url(#'+lightId+')'}/>
        <g data-editorial-source="one-portion" opacity={s.entrance}>
          <rect x="84" y="468" width="856" height="296" fill="#102532" stroke={C.muted} strokeOpacity=".45" strokeWidth="2"/>
          <path d="M110 616V700H914V616" fill="none" stroke={C.teal} strokeWidth="4" pathLength="1" strokeDasharray="1" strokeDashoffset={1-s.portion}/>
          <rect x="84" y="468" width="856" height="296" fill="none" stroke={C.risk} strokeWidth="4" opacity={s.conflict}/>
          <circle cx="512" cy="764" r="7" fill={C.paper}/>
        </g>
        <path data-editorial-claim="reserve" d="M512 764V904H282V1028" fill="none" stroke={C.teal} strokeWidth="6" pathLength="1" strokeDasharray="1" strokeDashoffset={1-s.firstClaim}/>
        <path data-editorial-claim="goal" d="M512 764V904H742V1028" fill="none" stroke={C.gold} strokeWidth="6" pathLength="1" strokeDasharray="1" strokeDashoffset={1-s.secondClaim}/>
        <rect x="84" y="1030" width="396" height="278" fill="#102532" stroke={C.teal} strokeWidth="3" opacity={.22+.78*s.firstClaim}/>
        <rect x="544" y="1030" width="396" height="278" fill="#102532" stroke={C.gold} strokeWidth="3" opacity={.22+.78*s.secondClaim}/>
        <path d="M84 1422H940" stroke={C.risk} strokeWidth="3" opacity={s.conflict}/>
      </svg>
      <Label text="Promised twice?" x={84} y={202} width={856} size={92}/>
      <Label text="YOUR SAVINGS" x={120} y={512} width={780} size={64} lines={1} opacity={s.entrance}/>
      <Label text="ONE PORTION" x={140} y={626} width={740} size={49} lines={1} color={C.teal} opacity={s.portion}/>
      <div data-editorial-not-spent={s.notSpent} style={{position:'absolute',left:84,top:802,opacity:s.notSpent,color:C.teal,fontSize:29,fontWeight:700}}>NOT SPENT</div>
      <Label text="FIRST CLAIM" x={116} y={1060} width={328} size={23} lines={1} color={C.teal} opacity={s.firstClaim}/>
      <Label text="EMERGENCY RESERVE" x={116} y={1120} width={328} size={46} opacity={.3+.7*s.firstClaim}/>
      <Label text="SECOND CLAIM" x={576} y={1060} width={328} size={23} lines={1} color={C.gold} opacity={s.secondClaim}/>
      <Label text="FUTURE GOAL" x={576} y={1120} width={328} size={46} opacity={.3+.7*s.secondClaim}/>
      <Label text="Two claims. The same portion." x={84} y={1454} width={856} size={45} color={C.risk} opacity={s.conflict}/>
    </div>
  </SceneShell>;
};

export function editorialPlanningDirection(scene:ManifestScene){
  const r=scene.visual_relation,d=r?.editorial_direction,c=d?.cue_times,words=scene.caption_words??[];
  if(r?.kind!=='reserve_readiness'||!d||![1,2].includes(d.version)||d.timing_source!=='final_voice_word_alignment'
    ||d.measurement!=='qualitative_no_amounts'||d.planning_action!==r.planning_desk_action
    ||r.planning_desk_action!==planningDeskAction(scene)||!c||!words.length
    ||Math.abs(d.duration_sec-(scene.timing.end_sec-scene.timing.start_sec))>.001||d.hold_seconds<.8
    ||!/^[0-9a-f]{64}$/.test(d.narration_hash))throw new Error('Missing or stale audio-bound editorial planning direction');
  if(words.map(w=>w.text).join(' ').split(/\s+/).join(' ')!==scene.narration.split(/\s+/).join(' '))
    throw new Error('Editorial planning words do not match the complete narration');
  let previous=scene.timing.start_sec;
  for(const w of words){
    if(!Number.isFinite(w.start_sec)||!Number.isFinite(w.end_sec)||w.start_sec<previous-.001
      ||w.end_sec<=w.start_sec||w.start_sec<scene.timing.start_sec-.001||w.end_sec>scene.timing.end_sec+.001)
      throw new Error('Editorial planning needs monotonic final voice alignment');
    previous=w.end_sec;
  }
  const roles=r.objects??[];
  if(!roles.some(o=>o.role==='reserve')||!roles.some(o=>o.role==='goal')||roles.some(o=>o.role==='expense'||o.label==='PLANNED BILL FUND')
    ||/[\d$€£%]/.test(scene.narration))throw new Error('Editorial planning is a qualitative two-purpose mechanism');
  const actual=(tokens:string[])=>{
    const w=words.find(w=>tokens.includes(token(w.text)));
    if(!w)throw new Error('Missing actual planning cue: '+tokens.join('/'));
    return w.start_sec-scene.timing.start_sec;
  };
  const check=(key:string,expected:number)=>{
    if(!Number.isFinite(c[key])||Math.abs(c[key]-expected)>.00001)throw new Error('Stale editorial planning cue: '+key);
  };
  let first:number,second:number;
  if(d.mode==='planning_claims'){
    const claim=editorialClaimsDirection(scene);
    check('first',claim.firstAt);check('second',claim.secondAt);check('portion',claim.savingsAt);
    if(claim.spentAt!==null)check('not_spent',claim.spentAt);
    first=claim.firstAt;second=claim.secondAt;
  }else if(d.mode==='purpose_naming'&&r.planning_desk_action==='write-purposes'){
    if(!roles.some(o=>o.role==='planning_notebook'))throw new Error('Naming requires one planning notebook');
    check('open',actual(['open']));check('reserve',actual(['emergency','reserve']));
    check('name',actual(['name']));check('goal',actual(['future','purchase','goal']));
    first=c.reserve;second=c.goal;
  }else if(d.mode==='portion_audit'&&r.planning_desk_action==='shared-balance'){
    if(!roles.some(o=>o.role==='shared_portion'))throw new Error('Audit requires the shared portion');
    for(const key of ['inspect','balance','portion','counted','both'])check(key,actual(key==='inspect'?['inspect','check']:[key]));
    first=c.portion;second=c.both;
  }else if(d.mode==='conditional_availability'&&r.planning_desk_action==='shared-balance'){
    if(!r.condition||!words.some(w=>token(w.text)==='if'))throw new Error('Conditional effect requires the stated if scenario');
    check('condition',actual(['emergency']));
    check('effect',Math.max(c.condition+.45,actual(['reduces','reduce','less','decreases'])));
    for(const [key,tokens] of [['source_context',['same','funds','portion']],['claim_context',['two','competing','claims']]] as const){
      if(c[key]!==undefined){
        check(key,actual([...tokens]));
        if(c[key]>=c.condition)throw new Error('Context attention cannot replace the narrated condition');
      }
    }
    first=c.condition;second=c.effect;
  }else if(d.mode==='goal_review'&&r.planning_desk_action==='revisit-goal'){
    if(!r.condition||!roles.some(o=>o.role==='goal_timeline'))throw new Error('Review requires an undated timeline and stated condition');
    check('choice',actual(['changes']));check('condition',actual(['used']));check('review',actual(['review','impact']));check('timeline',actual(['timeline']));
    if(c.timeline<c.condition+.4)throw new Error('Timeline review cannot precede its condition');
    first=c.condition;second=c.timeline;
  }else throw new Error('Unsupported editorial planning mode; no generic fallback');
  if(Math.abs(d.action_sec-first)>.00001||Math.abs(d.risk_sec-second)>.00001||second+.6+.8>d.duration_sec)
    throw new Error('Editorial planning direction has stale timing or insufficient reading hold');
  return {...d,cue_times:c};
}

export function editorialPlanningState(scene:ManifestScene,frame:number){
  const d=editorialPlanningDirection(scene),c=d.cue_times,t=frame/30;
  const show=(key:string,duration=.55)=>ease((t-c[key])/duration);
  if(d.mode==='purpose_naming')return {mode:d.mode,open:show('open',.6),reserve:show('reserve'),name:show('name'),goal:show('goal')};
  if(d.mode==='portion_audit')return {mode:d.mode,inspect:show('inspect'),balance:show('balance'),portion:show('portion'),counted:show('counted'),both:show('both')};
  if(d.mode==='conditional_availability'){
    const condition=show('condition',.45);
    const source=c.source_context===undefined?0:show('source_context');
    const claims=c.claim_context===undefined?0:show('claim_context');
    return {mode:d.mode,sourceAttention:source*(1-claims),claimAttention:claims*(1-condition),condition,effect:show('effect',.5)};
  }
  if(d.mode==='goal_review')return {mode:d.mode,choice:show('choice'),condition:show('condition'),review:ease((t-Math.max(c.review,c.condition+.4))/.55),timeline:show('timeline')};
  return {mode:d.mode,...editorialClaimsState(scene,frame)};
}

const PlanningFrame:React.FC<{scene:ManifestScene;brand:BrandSpec;mode:string;headline:string;children:React.ReactNode}>=
({scene,brand,mode,headline,children})=><SceneShell scene={scene} brand={{...brand,captionStyle:'plain_phrase'}} showBrandMark={false} compactCaptions>
  <div data-editorial-planning={mode} style={{position:'absolute',inset:0,background:'linear-gradient(180deg,#183241,#081523)'}}>
    <Label text={headline} x={84} y={202} width={856} size={82}/>
    {children}
  </div>
</SceneShell>;

export function editorialNamingLayout(open:number){
  // Retire the old source first, separate vertically, then expand horizontally.
  // A simultaneous diagonal expansion makes the two purpose cards collide.
  const retired=ease(open/.18),lift=ease((open-.18)/.42),spread=ease((open-.60)/.40);
  return {retired,page:spread,
    left:{x:84+44*spread,y:1030-470*lift,w:396+372*spread},
    right:{x:544-416*spread,y:1030-30*lift,w:396+372*spread}};
}

const NamingScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=({scene,brand})=>{
  const s=editorialPlanningState(scene,useCurrentFrame());
  const {left,right,retired,page}=editorialNamingLayout(s.open??0);
  return <PlanningFrame scene={scene} brand={brand} mode="purpose-naming" headline="Give each purpose a name.">
    <svg width="1080" height="1920" style={{position:'absolute',inset:0}} aria-label="One planning notebook with two named purposes">
      <g opacity={1-retired}><rect x="84" y="468" width="856" height="296" fill="#102532" stroke={C.risk} strokeWidth="4"/>
        <path d="M512 764V904H282V1028M512 764V904H742V1028" fill="none" stroke={C.muted} strokeWidth="5"/>
      </g>
      <g data-editorial-notebook="one-planning-page" opacity={page}><rect x="84" y="430" width="856" height="970" fill="#102532" stroke={C.muted} strokeWidth="2"/>
        <path d="M104 450V1380M128 510H896" stroke={C.gold} strokeWidth="3" opacity=".65"/>
      </g>
      <rect x={left.x} y={left.y} width={left.w} height="278" fill="#102532" stroke={C.teal} strokeWidth="3"/>
      <rect x={right.x} y={right.y} width={right.w} height="278" fill="#102532" stroke={C.gold} strokeWidth="3"/>
      <path data-editorial-mark="reserve" d={`M${left.x+32} ${left.y+220}h300`} pathLength="1" strokeDasharray="1" strokeDashoffset={1-(s.reserve??0)} stroke={C.teal} strokeWidth="6"/>
      <path data-editorial-mark="goal" d={`M${right.x+32} ${right.y+220}h300`} pathLength="1" strokeDasharray="1" strokeDashoffset={1-(s.goal??0)} stroke={C.gold} strokeWidth="6"/>
    </svg>
    <Label text="YOUR SAVINGS" x={120} y={512} width={780} size={64} lines={1} opacity={1-retired}/>
    <Label text="ONE PORTION" x={140} y={626} width={740} size={49} lines={1} color={C.teal} opacity={1-retired}/>
    <Label text="SAVINGS PLAN" x={128} y={460} width={768} size={33} lines={1} color={C.muted} opacity={page}/>
    <Label text="EMERGENCY RESERVE" x={left.x+32} y={left.y+90} width={328} size={46}/>
    <Label text="FUTURE GOAL" x={right.x+32} y={right.y+90} width={328} size={46}/>
    <Label text="UNEXPECTED NEEDS" x={530} y={650} width={320} size={31} color={C.teal} opacity={(s.reserve??0)*page}/>
    <Label text="NAME ONE PURCHASE" x={530} y={1090} width={320} size={31} color={C.gold} opacity={(s.name??0)*page}/>
  </PlanningFrame>;
};

const AuditScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=({scene,brand})=>{
  const s=editorialPlanningState(scene,useCurrentFrame());
  return <PlanningFrame scene={scene} brand={brand} mode="portion-audit" headline="Count this portion once.">
    <svg width="1080" height="1920" style={{position:'absolute',inset:0}} aria-label="Inspecting one portion against two possible claims">
      <rect x="84" y="520" width="856" height="780" fill="#102532" stroke={C.muted} strokeWidth="2"/>
      <rect data-editorial-attention="inspect-ledger" x="84" y="520" width="856" height="780" fill="none"
        stroke={C.paper} strokeWidth="4" opacity={.65*(s.inspect??0)*(1-(s.balance??0))}/>
      <path d="M112 670H912M390 548V1020M660 548V1020" fill="none" stroke={C.muted} strokeWidth="2" opacity=".5"/>
      <rect x="116" y="715" width="250" height="270" fill="none" stroke={C.teal} strokeWidth="4" opacity={s.balance??0}/>
      <rect x="116" y="715" width="250" height="270" fill="none" stroke={C.risk} strokeWidth="4" opacity={s.portion??0}/>
      <rect x="414" y="715" width="225" height="270" fill="none" stroke={C.teal} strokeWidth="3" opacity={s.counted??0}/>
      <rect x="679" y="715" width="225" height="270" fill="none" stroke={C.gold} strokeWidth="3" opacity={s.both??0}/>
      <path d="M526 985V1090H240V985" fill="none" stroke={C.teal} strokeWidth="5" pathLength="1" strokeDasharray="1" strokeDashoffset={1-(s.counted??0)}/>
      <path d="M790 985V1160H240V985" fill="none" stroke={C.gold} strokeWidth="5" pathLength="1" strokeDasharray="1" strokeDashoffset={1-(s.both??0)}/>
    </svg>
    <Label text="PORTION" x={130} y={574} width={240} size={29} lines={1} color={C.muted}/>
    <Label text="RESERVE" x={420} y={574} width={210} size={29} lines={1} color={C.teal}/>
    <Label text="GOAL" x={695} y={574} width={210} size={29} lines={1} color={C.gold}/>
    <Label text="SAME PORTION" x={140} y={790} width={208} size={40}/>
    <Label text="?" x={490} y={760} width={120} size={100} lines={1} color={C.teal} opacity={s.counted??0}/>
    <Label text="?" x={755} y={760} width={120} size={100} lines={1} color={C.gold} opacity={s.both??0}/>
    <Label text="ASSIGNED?" x={440} y={922} width={180} size={24} lines={1} color={C.muted} opacity={s.counted??0}/>
    <Label text="ASSIGNED?" x={705} y={922} width={180} size={24} lines={1} color={C.muted} opacity={s.both??0}/>
    <Label text="CHECK BOTH CLAIMS" x={128} y={1210} width={768} size={33} lines={1} color={C.risk} opacity={s.both??0}/>
    <Label text="Do the claims overlap?" x={84} y={1420} width={856} size={48} opacity={s.both??0}/>
  </PlanningFrame>;
};

const ConditionalScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=({scene,brand})=>{
  const s=editorialPlanningState(scene,useCurrentFrame()),condition=s.condition??0,effect=s.effect??0;
  return <PlanningFrame scene={scene} brand={brand} mode="conditional-availability" headline="If an emergency occurs…">
    <svg width="1080" height="1920" style={{position:'absolute',inset:0}} aria-label="Conditional reserve use leaves less for the other purpose, without an invented amount">
      <rect x="84" y="468" width="856" height="296" fill="#102532" stroke={C.muted} strokeWidth="2"/>
      <rect data-editorial-attention="same-source" x="84" y="468" width="856" height="296" fill="none"
        stroke={C.paper} strokeWidth="5" opacity={.8*(s.sourceAttention??0)}/>
      <path d="M512 764V904H282V1028" fill="none" stroke={C.teal} strokeWidth="6"/>
      <path d="M512 764V904H742V1028" fill="none" stroke={C.gold} strokeWidth="6" strokeDasharray={effect>0?'12 12':undefined} opacity={1-.5*effect}/>
      <rect x="84" y="1030" width="396" height="278" fill="#102532" stroke={C.teal} strokeWidth="3"/>
      <rect data-editorial-attention="reserve-claim" x="84" y="1030" width="396" height="278" fill="none"
        stroke={C.teal} strokeWidth="6" opacity={s.claimAttention??0}/>
      <rect x="84" y="1030" width="396" height="278" fill="none" stroke={C.risk} strokeWidth="5" opacity={condition}/>
      <rect x="544" y="1030" width="396" height="278" fill="#102532" stroke={C.gold} strokeWidth="3" opacity={1-.35*effect}/>
      <rect data-editorial-attention="goal-claim" x="544" y="1030" width="396" height="278" fill="none"
        stroke={C.gold} strokeWidth="6" opacity={s.claimAttention??0}/>
    </svg>
    <Label text="THE SAME PORTION" x={120} y={512} width={780} size={58} lines={1}/>
    <Label text="TWO COMPETING CLAIMS" x={120} y={640} width={780} size={34} lines={1} color={C.muted}/>
    <Label text="CONDITIONAL SCENARIO" x={84} y={810} width={856} size={29} lines={1} color={C.gold}/>
    <Label text="EMERGENCY RESERVE" x={116} y={1120} width={328} size={46}/>
    <Label text="FUTURE GOAL" x={576} y={1120} width={328} size={46}/>
    <Label text="USED IF NEEDED" x={116} y={1250} width={328} size={24} lines={1} color={C.risk} opacity={condition}/>
    <Label text="LESS AVAILABLE" x={576} y={1250} width={328} size={24} lines={1} color={C.gold} opacity={effect}/>
    <Label text="Less for the other purpose." x={84} y={1454} width={856} size={45} color={C.risk} opacity={effect}/>
  </PlanningFrame>;
};

const GoalReviewScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=({scene,brand})=>{
  const s=editorialPlanningState(scene,useCurrentFrame());
  return <PlanningFrame scene={scene} brand={brand} mode="goal-review" headline="Which plan changes?">
    <svg width="1080" height="1920" style={{position:'absolute',inset:0}} aria-label="Reviewing an undated future-goal timeline if the reserve is used">
      <rect x="84" y="480" width="856" height="218" fill="#102532" stroke={C.teal} strokeWidth="3"/>
      <path d="M512 698V938" fill="none" stroke={C.gold} strokeWidth="5" pathLength="1" strokeDasharray="1" strokeDashoffset={1-(s.review??0)}/>
      <rect x="84" y="940" width="856" height="490" fill="#102532" stroke={C.gold} strokeWidth="3"/>
      <rect data-editorial-attention="which-plan" x="84" y="940" width="856" height="490" fill="none"
        stroke={C.paper} strokeWidth="5" opacity={.65*(s.choice??0)*(1-(s.condition??0))}/>
      <g data-editorial-timeline="undated-question" opacity={s.timeline??0}>
        <path d="M148 1270H870" stroke={C.muted} strokeWidth="4"/>
        <circle cx="148" cy="1270" r="8" fill={C.gold}/><circle cx="870" cy="1270" r="8" fill={C.gold}/>
      </g>
    </svg>
    <Label text="EMERGENCY RESERVE" x={120} y={520} width={780} size={49} lines={1}/>
    <Label text="IF USED" x={120} y={620} width={780} size={30} lines={1} color={C.teal} opacity={s.condition??0}/>
    <Label text="FUTURE GOAL" x={120} y={990} width={780} size={58} lines={1}/>
    <Label text="REVIEW THE TIMELINE" x={120} y={1110} width={780} size={35} lines={1} color={C.gold} opacity={s.review??0}/>
    <Label text="?" x={824} y={1170} width={75} size={74} lines={1} color={C.gold} opacity={s.timeline??0}/>
    <Label text="WHAT CHANGES?" x={148} y={1320} width={720} size={31} lines={1} color={C.muted} opacity={s.timeline??0}/>
  </PlanningFrame>;
};

export const EditorialPlanningScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=props=>{
  const mode=editorialPlanningDirection(props.scene).mode;
  if(mode==='planning_claims')return <ClaimsScene {...props}/>;
  if(mode==='purpose_naming')return <NamingScene {...props}/>;
  if(mode==='portion_audit')return <AuditScene {...props}/>;
  if(mode==='conditional_availability')return <ConditionalScene {...props}/>;
  if(mode==='goal_review')return <GoalReviewScene {...props}/>;
  throw new Error('Unsupported editorial planning direction');
};
