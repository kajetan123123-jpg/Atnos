import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import type {BrandSpec, ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText, MeasuredSvgText} from './typography';
import {EditorialGarage} from './WBEditorialWorld';
import {WBPlanningDeskScene, supportsPlanningDesk} from './WBPlanningDeskScene';
import {CarDrawing, ExpenseSheet, ReserveDrawing, GoalDrawing, ReserveObjectDefs, INK, PAPER, GOLD, BLUE} from './ReserveObjects';

export const RESERVE_STAGES = ['need', 'identify', 'access', 'separate', 'review'] as const;
export type ReserveStage = (typeof RESERVE_STAGES)[number];
export const educationalQuestion = (stage:ReserveStage):string => ({need:'Your car breaks down. Now what?',identify:'Where is your reserve?',access:'Can you actually access it?',separate:'Same money. Different jobs.',review:'Would your plan still work?'}[stage]);

export const reserveStageFor = (scene: ManifestScene): ReserveStage => {
  const stage = scene.visual_relation?.stage;
  if (scene.visual_relation?.kind !== 'reserve_readiness' || !RESERVE_STAGES.includes(stage as ReserveStage)) {
    throw new Error(`Reserve scene ${scene.scene_id} requires its explicit meaning stage`);
  }
  return stage as ReserveStage;
};

/** Reveal at the spoken cue, with enough time to read the complete comparison. */
export const reserveCueFrame = (scene: ManifestScene, words: string[], fallback: number, earlyCapFrames=75): number => {
  const timed = scene.caption_words?.find((item) => words.includes(item.text.toLowerCase().replace(/[^a-z]/g, '')));
  const intended = timed ? Math.round((timed.start_sec - scene.timing.start_sec) * 30) : fallback;
  const frames = Math.round((scene.timing.end_sec - scene.timing.start_sec) * 30);
  const latest = Math.max(0, Math.min(earlyCapFrames, frames - 90));
  return Math.max(0, Math.min(Math.max(12, intended), latest));
};

/** Explicit object roles choose a drawing; unspecified needs never become invented bills. */
export const reserveExpenseFor = (scene: ManifestScene): 'car' | 'medical' | 'home' | 'unspecified' => {
  const label = scene.visual_relation?.objects?.find(item => item.role === 'expense')?.label?.toLowerCase() ?? '';
  return label.includes('car repair') ? 'car' : label.includes('medical') ? 'medical' : label.includes('home repair') ? 'home' : 'unspecified';
};

/** One editorial world, distinct shots for the situation, check, contrast and review. */
export const ReserveReadinessScene: React.FC<{scene:ManifestScene;brand:BrandSpec}> = ({scene,brand}) => {
  const frame=useCurrentFrame(), stage=reserveStageFor(scene), expense=reserveExpenseFor(scene);
  if (brand.illustrationSystem==='wb_planning_desk_v1' || brand.illustrationSystem==='wb_planning_desk_painted_v1') {
    if (!supportsPlanningDesk(scene)) throw new Error('Unsupported planning-desk scene; no generic illustration fallback');
    return <WBPlanningDeskScene scene={scene} brand={brand}/>;
  }
  const educational=brand.presentation==='educational_illustration';
  const editorialGarage=educational && brand.illustrationSystem==='wb_editorial_vector_v1' && stage==='need' && expense==='car';
  const label=scene.visual_relation?.objects?.find(item => item.role==='expense')?.label ?? 'UNEXPECTED NEED';
  const goalLabel=scene.visual_relation?.objects?.find(item => item.role==='goal')?.label ?? 'FUTURE GOAL';
  const plannedBill=label==='ANNUAL BILL';
  const billFund=goalLabel==='PLANNED BILL FUND';
  const id=`reserve-${scene.scene_id.replace(/[^a-zA-Z0-9_-]/g,'')}`;
  const cue=(words:string[],fallback:number) => reserveCueFrame(scene,words,fallback);
  const phraseCue=(words:string[],fallback:number) => reserveCueFrame(scene,words,fallback,
    Math.round((scene.timing.end_sec-scene.timing.start_sec)*30));
  const reveal=(delay:number) => interpolate(frame,[delay,delay+16],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp',easing:t=>1-Math.pow(1-t,3)});
  const appearance=(delay:number) => ({opacity:reveal(delay),transform:`translate(0 ${16*(1-reveal(delay))})`});
  const isNeed=stage==='need', isIdentify=stage==='identify';
  const termsCue=Math.min(phraseCue(['waiting','notice','periods'],30),phraseCue(['penalties','fees','charges'],70));
  const reviewStart=cue(['review'],12);
  const reviewMoveStart=Math.max(reviewStart+24,phraseCue(['circumstances'],72)-18);
  const reviewEnd=Math.max(reviewMoveStart+36,phraseCue(['change','needs'],108));
  const reviewX=interpolate(frame,[reviewMoveStart,reviewEnd],[238,708],{
    extrapolateLeft:'clamp',extrapolateRight:'clamp',easing:t=>t*t*(3-2*t)});
  const kicker=isNeed?'WHEN LIFE INTERRUPTS':isIdentify?'MAKE THE NEED CONCRETE':stage==='access'?'READINESS IS A CHECK':stage==='separate'?'TWO JOBS FOR YOUR MONEY':'WHEN LIFE CHANGES';
  return <SceneShell scene={scene} brand={brand}>
    <div style={{position:'absolute',left:72,right:72,top:232}}>
      {!educational ? <div style={{fontSize:25,fontWeight:700,letterSpacing:4.5,color:brand.accent}}>{kicker}</div> : null}
      <div style={{marginTop:24,color:brand.text}}><MeasuredText text={educational ? educationalQuestion(stage) : scene.headline??''} width={936} maxSize={educational?94:80} minSize={50} maxLines={2}/></div>
    </div>
    <svg width="936" height="860" viewBox="0 0 936 860" aria-label={`Reserve readiness: ${stage}`}
      style={{position:'absolute',left:72,top:educational?460:520,overflow:'visible',
        ...(brand.background==='#171A3B' ? {'--reserve-ink':brand.background,'--reserve-paper':brand.text,
          '--reserve-gold':brand.accent,'--reserve-blue':brand.accentSoft,'--reserve-mint':brand.muted,
          '--reserve-rust':'#ED735C'} : {})} as React.CSSProperties}>
      <ReserveObjectDefs id={id}/>
      {editorialGarage ? <EditorialGarage id={id} progress={reveal(cue(['repair','today'],18))}/> : null}
      <path d="M-15 418 Q450 331 961 469 L951 837 H-15Z" fill={`url(#${id}-light)`}/>
      {educational && isNeed && !editorialGarage ? <g data-wb-object="curiosity-question" transform="translate(125 110) rotate(-7)"><circle r="68" fill={PAPER} stroke={GOLD} strokeWidth="3"/><text y="25" textAnchor="middle" fill={INK} fontSize="82" fontWeight="900">?</text></g> : null}
      {!editorialGarage && (isNeed || isIdentify) ? <>
        {expense==='car' ? <g transform={isNeed?'translate(-6 44) scale(1.09)':'translate(-8 25) scale(.58)'}><CarDrawing id={id}/></g> :
          <g transform="translate(465 263) scale(1.08)"><ExpenseSheet label={label} id={id} medical={expense==='medical'} planned={plannedBill} focus={scene.visual_relation?.bill_focus} progress={reveal(phraseCue(['list','record','write'],20))}/></g>}
        {expense==='car' ? <g data-wb-shot={isIdentify?'expense-close-up':'expense-context'} opacity={reveal(cue(['repair','expense','name'],isNeed?18:8))} transform={isNeed?'translate(702 378) rotate(5) scale(.73)':'translate(646 379) rotate(-4) scale(1.35)'}><ExpenseSheet label={label} id={id}/></g> : null}
        {isIdentify ? <g {...appearance(phraseCue(['locate','reserve'],42))}>
          <g transform="translate(214 510) rotate(-5) scale(.72)"><ReserveDrawing id={id}/></g>
          <text x="214" y="631" textAnchor="middle" fill={PAPER} fontSize="31" fontWeight="900">READY RESERVE</text>
        </g> : null}
        {!educational ? <g {...appearance(cue(['today','essential','reserve'],isNeed?40:36))}>
          <path d="M50 670 Q469 655 889 670 L889 805 H50Z" fill={PAPER} stroke={GOLD} strokeWidth="2"/>
          <text x="83" y="725" fill={INK} fontSize="39" fontWeight="900">{isNeed?'COULD YOU COVER IT TODAY?':'NAME YOUR ESSENTIAL NEED'}</text>
          <text x="85" y="775" fill={INK} fontSize="27" fontWeight="700">{isNeed?'READY RESERVE — CHECK YOUR ACCESS':'NAME THE NEED. CHECK YOUR RESERVE.'}</text>
        </g> : null}
      </> : null}
      {stage==='access' && scene.visual_relation?.access_focus!=='terms' ? <>
        <g transform="translate(189 185) rotate(-5) scale(.78)"><ReserveDrawing id={id}/></g>
        <text x="357" y="151" fill={PAPER} fontSize="47" fontWeight="900">READY RESERVE</text>
        {!educational ? <text x="359" y="198" fill={GOLD} fontSize="28" fontWeight="700">CHECK, DON'T ASSUME</text> : null}
        <path d="M85 349 L876 333 L881 795 L86 814Z" fill={`url(#${id}-paper)`} stroke={GOLD} strokeWidth="2"/>
        {['WHERE IS IT HELD?','HOW DO YOU ACCESS IT?','WHAT CONDITIONS APPLY?'].map((text,i)=><g key={text} {...appearance(phraseCue([['held','locate','where'],['access','withdraw'],['conditions','restrictions','fees']][i],24+i*19))}>
          <rect x="130" y={399+i*124} width="31" height="31" rx="3" fill="none" stroke={BLUE} strokeWidth="3"/>
          <text x="190" y={430+i*124} fill={INK} fontSize="34" fontWeight="700">{text}</text>
          <path d={`M191 ${454+i*124} H790`} stroke={BLUE} strokeWidth="1" opacity=".25"/>
        </g>)}
        {!educational ? <text x="128" y="776" fill={INK} fontSize="26" fontWeight="700">CHECK THE TERMS BEFORE YOU NEED IT</text> : null}
      </> : null}
      {stage==='access' && scene.visual_relation?.access_focus==='terms' ? <g data-wb-shot="own-terms-close-up">
        <g transform="translate(160 135) rotate(-5) scale(.6)"><ReserveDrawing id={id}/></g>
        <text x="306" y="122" fill={PAPER} fontSize="42" fontWeight="900">YOUR ACTUAL TERMS</text>
        <text x="306" y="166" fill={GOLD} fontSize="28" fontWeight="700">QUESTIONS, NOT ASSUMPTIONS</text>
        <path d="M96 236 Q455 220 840 236 L855 729 L99 741Z" fill={`url(#${id}-paper)`} stroke={GOLD} strokeWidth="3"/>
        <g data-wb-action="ask-before-assuming" opacity={1-reveal(termsCue-16)}>
          <text x="470" y="491" textAnchor="middle" fill={BLUE} fontSize="175" fontWeight="900">?</text>
          <MeasuredSvgText text="WHAT COULD CHANGE ACCESS?" x={470} y={568} width={650} maxSize={31} minSize={27} maxLines={1} weight={700} fill={INK}/>
        </g>
        <g {...appearance(phraseCue(['waiting','notice','periods'],30))} data-wb-check="waiting-time-question">
          <circle cx="242" cy="363" r="78" fill={PAPER} stroke={BLUE} strokeWidth="8"/>
          <path d="M242 308 V364 L280 384" fill="none" stroke={INK} strokeWidth="9" strokeLinecap="round"/>
          <MeasuredSvgText text="WAITING TIME?" x={598} y={350} width={450} maxSize={38} minSize={34} maxLines={1} fill={INK}/>
          <MeasuredSvgText text="IF YOUR TERMS REQUIRE IT" x={598} y={397} width={450} maxSize={30} minSize={26} maxLines={1} weight={700} fill={INK}/>
        </g>
        <g {...appearance(phraseCue(['penalties','fees','charges'],70))} data-wb-check="charges-question">
          <path d="M170 509 L300 502 L307 635 L166 641Z" fill={PAPER} stroke={BLUE} strokeWidth="4"/>
          <path d="M191 535 H281 M191 558 H273 M191 581 H244" fill="none" stroke={BLUE} strokeWidth="4"/>
          <MeasuredSvgText text="ANY CHARGES?" x={598} y={552} width={450} maxSize={38} minSize={34} maxLines={1} fill={INK}/>
          <MeasuredSvgText text="READ YOUR AGREEMENT" x={598} y={598} width={450} maxSize={30} minSize={26} maxLines={1} weight={700} fill={INK}/>
        </g>
        <text x="470" y="696" textAnchor="middle" fill={INK} fontSize="27" fontWeight="700">CHECK WHAT APPLIES TO YOU</text>
      </g> : null}
      {stage==='separate' || stage==='review' ? <>
        <g transform="translate(238 330) rotate(-4) scale(1.06)"><ReserveDrawing id={id}/></g>
        <g opacity={stage==='review'?1:reveal(cue(['future','longer','rather','separation','bill','scheduled'],24))} transform="translate(708 330) rotate(4) scale(1.08)">{billFund ? <ExpenseSheet label="PLANNED BILL" id={id} planned/> : <GoalDrawing id={id}/>}</g>
        <path d="M470 170 V591" fill="none" stroke={GOLD} opacity=".55" strokeWidth="2" strokeDasharray="6 11"/>
        <text x="238" y="545" textAnchor="middle" fill={PAPER} fontSize="39" fontWeight="900">READY RESERVE</text>
        <text x="238" y="590" textAnchor="middle" fill={GOLD} fontSize="26" fontWeight="700">UNEXPECTED NEEDS</text>
        <g opacity={stage==='review'?1:reveal(cue(['future','longer','rather','separation'],24))}>
          <MeasuredSvgText text={goalLabel} x={708} y={545} width={410} maxSize={39} minSize={29} maxLines={1} fill={PAPER}/>
          <text x="708" y="590" textAnchor="middle" fill={GOLD} fontSize="26" fontWeight="700">{billFund?'KNOWN DUE DATE':'LONGER-TERM PLANS'}</text>
        </g>
        {stage==='review' ? <g data-wb-action="inspect-both-roles" opacity={reveal(reviewStart)}
          transform={`translate(${reviewX} 330)`} fill="none" stroke={GOLD} strokeWidth="5">
          <circle r="96" fill={PAPER} fillOpacity=".09" stroke={INK} strokeWidth="22"/>
          <circle r="96" stroke={GOLD} strokeWidth="10"/>
          <circle r="85" stroke={PAPER} strokeWidth="2" opacity=".8"/>
          <path d="M-69 69 L-139 139" stroke={INK} strokeWidth="31" strokeLinecap="round"/>
          <path d="M-70 70 L-136 136" stroke={GOLD} strokeWidth="19" strokeLinecap="round"/>
        </g> : null}
        {!educational ? <g {...appearance(cue(stage==='review'?['review','circumstances','changes']:['separate','different','readiness'],42))}>
          <path d="M82 669 Q470 655 854 667 V804 H82Z" fill={PAPER} stroke={GOLD} strokeWidth="2"/>
          <text x="469" y="726" textAnchor="middle" fill={INK} fontSize="35" fontWeight="900">{stage==='review'?'LIFE CHANGES. REVIEW BOTH.':'DIFFERENT PURPOSES. CLEAR LABELS.'}</text>
          <text x="469" y="775" textAnchor="middle" fill={INK} fontSize="26" fontWeight="700">{stage==='review'?'RECHECK YOUR NEEDS AND ACTUAL ACCESS':'LABEL EACH PURPOSE CLEARLY'}</text>
        </g> : null}
      </> : null}
    </svg>
  </SceneShell>;
};
