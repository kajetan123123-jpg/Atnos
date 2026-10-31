import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import type {BrandSpec, ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText, MeasuredSvgText} from './typography';
import {AccountPhone,WritingHand} from './WBPlanningDeskHands';
import {WBPaintedDefinitions,WBPaintedMaterials} from './WBPaintedMaterials';

const NAVY='#171A3B', INDIGO='#343265', TEAL='#18AAA6', MINT='#AFE5CC', CREAM='#FFF2D5', AMBER='#F2B54A', CORAL='#ED735C';
const doublePromiseHook=(scene:ManifestScene)=>scene.visual_relation?.stage==='identify'
  && /\b(?:promised|assigned|counted)\b.{0,40}\btwice\b.{0,40}\b(?:goals?|purposes?|plans?)\b/i.test(scene.narration);
const rotatePoint=(x:number,y:number,cx:number,cy:number,angle:number)=>{
  const a=angle*Math.PI/180;return {x:cx+(x-cx)*Math.cos(a)-(y-cy)*Math.sin(a),y:cy+(x-cx)*Math.sin(a)+(y-cy)*Math.cos(a)};
};
export const planningDeskPurposeAnchors=()=>({
  reserve:rotatePoint(355,1240,355,1151,-9),
  goal:rotatePoint(725,1248,725,1181,8),
});
export const supportsPlanningDesk=(scene:ManifestScene)=>scene.visual_relation?.kind==='reserve_readiness'
  && ['identify','separate','review'].includes(scene.visual_relation.stage??'')
  && !scene.visual_relation.objects?.some(o=>o.role==='goal' && o.label==='PLANNED BILL FUND')
  && (!scene.visual_relation.planning_desk_action || scene.visual_relation.planning_desk_action===planningDeskAction(scene));

const spokenFrame=(scene:ManifestScene,tokens:string[],fallback:number)=>{
  const word=scene.caption_words?.find(w=>tokens.includes(w.text.toLowerCase().replace(/[^a-z]/g,'')));
  return word?Math.max(0,Math.round((word.start_sec-scene.timing.start_sec)*30)):fallback;
};
const settle=(frame:number,at:number,frames=20)=>interpolate(frame,[at,at+frames],[0,1],
  {extrapolateLeft:'clamp',extrapolateRight:'clamp',easing:t=>t*t*(3-2*t)});
export const planningDeskReviewFocus=(scene:ManifestScene)=>{
  if(doublePromiseHook(scene))return 'shared-balance';
  if(scene.visual_relation?.stage!=='review')return 'roles';
  const text=scene.narration.toLowerCase();
  if(/double[- ]count|\boverlap\b|\btwice\b|\b(?:portion|money|funds)\b.{0,40}\bcounted\s+for\s+both\b|\bsame\s+(?:money|funds|portion)\b.{0,48}\b(?:both|two(?:\s+competing)?\s+(?:plans|claims|purposes))\b/.test(text))return 'shared-balance';
  if(/\b(already|committed|assigned)\b/.test(text))return 'commitments';
  if(/\bwhich plan\b.{0,20}\bchanges?\b|\bimpact\b.{0,35}\b(?:goal|timeline)\b/.test(text))return 'consequence';
  if(/confus|\bdistinct\b|\bdifferent\b|\bmix/.test(text))return 'distinction';
  return 'roles';
};
export const planningDeskAction=(scene:ManifestScene)=>{
  if(scene.visual_relation?.stage==='identify')return doublePromiseHook(scene)?'shared-balance':/\b(?:account|balance)\b/i.test(scene.narration)?'account-to-purposes':'purpose-context';
  if(scene.visual_relation?.stage==='separate')return 'write-purposes';
  return ({commitments:'existing-assignments','shared-balance':'shared-balance',consequence:'revisit-goal',distinction:'purpose-distinction',roles:'review-both'} as const)[planningDeskReviewFocus(scene)];
};
export const planningDeskHeadline=(scene:ManifestScene)=>{
  const stage=scene.visual_relation?.stage,focus=planningDeskReviewFocus(scene);
  return doublePromiseHook(scene)?'Promised twice?':stage==='identify' && (/\btwo\s+(?:distinct\s+)?jobs\b/i.test(scene.narration) || /\bbalance\b.*\b(?:both|twice)\b/i.test(scene.narration))
    ? 'One balance. Two jobs.' : stage==='separate' && /\bname\b/i.test(scene.narration)
      ? 'Name both purposes.' : stage==='review' && focus==='commitments' ? 'What is already assigned?'
      : stage==='review' && focus==='shared-balance' ? 'Count each portion once.'
      : stage==='review' && focus==='consequence' ? 'Which plan would change?'
      : stage==='review' && focus==='distinction' ? 'Keep the two jobs clear.' : scene.headline??'';
};
export const planningDeskConsequenceState=(scene:ManifestScene,frame:number)=>{
  // Checking double-counting does not imply that an emergency actually happened.
  // In a conditional sentence the effect must not precede its narrated condition.
  const explainsEmergencyEffect=/\bemergency\b/i.test(scene.narration)
    && /\b(?:reduces?|reduced|reducing|less|decreases?|decreased)\b.{0,45}\b(?:available|availability|money|funds)\b/i.test(scene.narration);
  const claimAt=spokenFrame(scene,['emergency'],55);
  const effectAt=Math.max(claimAt+14,spokenFrame(scene,['reduces','reduce','reducing','reduced','less','decreases','decreased'],95));
  return {
    emergencyClaim: explainsEmergencyEffect?settle(frame,claimAt,14):0,
    lessAvailable: explainsEmergencyEffect?settle(frame,effectAt,16):0,
    reviewDate: planningDeskReviewFocus(scene)==='consequence'?settle(frame,spokenFrame(scene,['impact','timeline'],70),18):0,
  };
};
export const planningDeskSharedConnections=(scene:ManifestScene,frame:number)=>{
  const firstAt=spokenFrame(scene,['same','portion','promised','double','overlap','twice'],24);
  const secondAt=Math.max(firstAt+12,spokenFrame(scene,['both','two','twice'],firstAt+24));
  return {reserve:settle(frame,firstAt,18),goal:settle(frame,secondAt,18)};
};
export const planningDeskCamera=(scene:ManifestScene,frame:number)=>{
  if(doublePromiseHook(scene))return {scale:1.4,x:535,y:1180,shot:'shared-portion-close-up'};
  if(scene.visual_relation?.stage==='identify' && /\b(?:account|balance)\b/i.test(scene.narration)){
    const pull=settle(frame,spokenFrame(scene,['but','check','counted'],60));
    return {scale:2.05-1.05*pull,x:865-325*pull,y:716+244*pull,shot:pull<1?'one-account-close-up':'two-purposes-context'};
  }
  if(scene.visual_relation?.stage==='separate'){
    const close=settle(frame,spokenFrame(scene,['open','name'],8));
    return {scale:1+.22*close,x:540,y:960+220*close,shot:close<1?'move-to-notation':'notation-close-up'};
  }
  if(scene.visual_relation?.stage==='review' && planningDeskReviewFocus(scene)==='shared-balance'){
    // The shared claim is the subject of this shot. Keep both full label regions
    // inside the phone-safe width, without resetting a zoom between matching scenes.
    return {scale:1.4,x:535,y:1180,shot:'shared-portion-close-up'};
  }
  if(scene.visual_relation?.stage==='review' && planningDeskReviewFocus(scene)==='consequence'){
    const focus=planningDeskConsequenceState(scene,frame).reviewDate;
    return {scale:1.22+.08*focus,x:540+25*focus,y:1180+35*focus,shot:focus<1?'review-both-purposes':'review-goal-date'};
  }
  return {scale:1.22,x:540,y:1180,shot:'review-both-purposes'};
};

/** Original, count-controlled illustration layers; candidate appearance is a separate review. */
export const WBPlanningDeskScene:React.FC<{scene:ManifestScene;brand:BrandSpec}>=({scene,brand})=>{
  if (!supportsPlanningDesk(scene)) throw new Error('Planning desk requires a supported purpose-comparison scene');
  const frame=useCurrentFrame();
  const id='desk-'+scene.scene_id.replace(/[^\w-]/g,'');
  const stage=scene.visual_relation!.stage;
  const painted=brand.illustrationSystem==='wb_planning_desk_painted_v1';
  const cue=(tokens:string[],fallback:number)=>spokenFrame(scene,tokens,fallback);
  const reveal=(at:number)=>interpolate(frame,[at,at+14],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp',easing:t=>t*t*(3-2*t)});
  const left=stage==='identify'?cue(['two','distinct'],30):stage==='separate'?cue(['reserve','emergency'],20):0;
  const right=stage==='identify'?left+14:stage==='separate'?cue(['planned','future'],65):0;
  const inspect=stage==='review'?reveal(cue(['both','difference','roles'],24)):0;
  const plannedAction=scene.visual_relation?.planning_desk_action??planningDeskAction(scene);
  const sharedClaim=plannedAction==='shared-balance';
  const reviewFocus=({'existing-assignments':'commitments','shared-balance':'shared-balance','revisit-goal':'consequence','purpose-distinction':'distinction'} as Record<string,string>)[plannedAction]??planningDeskReviewFocus(scene);
  const reviewBuild=stage==='review'?reveal(cue(['committed','assigned','overlap','counting','confusing','distinct','different'],30)):0;
  const consequence=planningDeskConsequenceState(scene,frame);
  const sharedConnections=planningDeskSharedConnections(scene,frame);
  const camera=planningDeskCamera(scene,frame);
  const leftStroke=reveal(left),rightStroke=reveal(right);
  const lift=settle(frame,left+16,Math.max(14,right-left-16));
  const purposeAnchors=planningDeskPurposeAnchors();
  const lp=rotatePoint(232+229*leftStroke,1240,355,1151,-9);
  const rp=rotatePoint(610+227*rightStroke,1248,725,1181,8);
  const handX=lp.x+(rp.x-lp.x)*lift,handY=lp.y+(rp.y-lp.y)*lift-30*Math.sin(Math.PI*lift);
  const handOpacity=stage==='separate'?reveal(cue(['name'],10))*(1-settle(frame,right+45,20)):0;
  const headline=planningDeskHeadline(scene);
  return <SceneShell scene={scene} brand={brand} showBrandMark={false} compactCaptions>
    <svg width="1080" height="1920" viewBox="0 0 1080 1920" aria-label="Illustrated desk with one notebook and two planning purposes"
      style={{position:'absolute',inset:0}} data-wb-world="planning-desk-candidate-v1">
      <defs>
        {painted?<WBPaintedDefinitions id={id}/>:null}
        <linearGradient id={id+'-wall'} x2=".7" y2="1"><stop stopColor={INDIGO}/><stop offset=".55" stopColor="#2D4561"/><stop offset="1" stopColor={NAVY}/></linearGradient>
        <linearGradient id={id+'-sky'} x2=".1" y2="1"><stop stopColor="#A3E6D6"/><stop offset=".58" stopColor="#EECFAA"/><stop offset="1" stopColor={CORAL}/></linearGradient>
        <linearGradient id={id+'-wood'} x2=".6" y2="1"><stop stopColor="#267D88"/><stop offset="1" stopColor="#154B65"/></linearGradient>
        <linearGradient id={id+'-paper'} x2=".8" y2="1"><stop stopColor="#FFF9E9"/><stop offset="1" stopColor="#E8D5B1"/></linearGradient>
        <radialGradient id={id+'-glow'}><stop stopColor={AMBER} stopOpacity=".2"/><stop offset="1" stopColor={AMBER} stopOpacity="0"/></radialGradient>
        <clipPath id={id+'-window'}><path d="M590 428 Q594 404 620 406 H985 V941 H590Z"/></clipPath>
      </defs>
      <g data-wb-shot={camera.shot} data-wb-planned-action={plannedAction} transform={`translate(540 960) scale(${camera.scale}) translate(${-camera.x} ${-camera.y})`}>
      <g data-wb-paint={painted?'gouache-candidate':'clean-vector'} filter={painted?'url(#'+id+'-brush-edge)':undefined}>
      <path d="M0 0 H1080 V1920 H0Z" fill={'url(#'+id+'-wall)'}/>
      {painted?<WBPaintedMaterials id={id} surface="wall"/>:null}
      <path d="M-50 782 Q394 740 1080 913 V1190 H0Z" fill="#395E73" opacity=".55"/>
      <path d="M0 870 L190 841 L170 510 L0 528Z" fill="#25283F"/>
      <g data-wb-layer="shelf">
        <path d="M-18 614 L437 602 L448 627 L-18 643Z" fill="#6A6171"/>
        <path d="M12 454 L55 450 L64 611 L16 618Z" fill={CORAL}/>
        <path d="M55 473 L98 469 L108 611 L62 614Z" fill="#CEAA80"/>
        <path d="M105 431 L146 427 L155 608 L109 611Z" fill={MINT}/>
        <path d="M155 491 L199 487 L207 606 L157 609Z" fill="#55779E"/>
        <path d="M223 464 L262 452 L313 601 L267 612Z" fill={AMBER}/>
        <path d="M290 515 L335 511 L343 603 L299 606Z" fill={TEAL}/>
        <path d="M18 485 L49 480 M64 514 L93 510 M114 471 L139 467 M232 488 L254 480" stroke={CREAM} strokeWidth="4" opacity=".4"/>
      </g>
      <g data-wb-layer="window">
        <path d="M568 389 Q579 380 598 382 H1006 V964 H568Z" fill={NAVY}/>
        <g clipPath={'url(#'+id+'-window)'}>
          <path d="M590 406 H985 V941 H590Z" fill={'url(#'+id+'-sky)'}/>
          {painted?<WBPaintedMaterials id={id} surface="sky"/>:null}
          <circle cx="863" cy="510" r="57" fill={CREAM} opacity=".82"/>
          <path d="M536 803 Q669 636 786 764 Q901 663 1080 726 V941 H540Z" fill="#507982"/>
          <path d="M579 873 Q714 747 813 842 Q942 728 1020 829 V955 H580Z" fill="#305568"/>
          <path d="M692 825 V710 H738 V825 M782 850 V670 H820 V850 M899 828 V737 H941 V828" fill="#314C66"/>
          <path d="M699 721 V754 M730 721 V754 M790 682 V715 M812 731 V762" stroke={AMBER} opacity=".38" strokeWidth="5"/>
          <path d="M620 410 L914 941 H955 L661 410Z" fill={CREAM} opacity=".11"/>
        </g>
        <path d="M777 400 V945 M584 662 H989" stroke="#26374E" strokeWidth="13"/>
        <path d="M565 946 L1015 941 L1031 970 H560Z" fill="#CDBDA4"/>
      </g>
      <path d="M575 968 L965 1630 L389 1489 L490 974Z" fill={AMBER} opacity=".09"/>
      <g data-wb-layer="lamp">
        <path d="M243 719 L299 593 L414 710" stroke={NAVY} strokeWidth="20" fill="none" strokeLinecap="round"/>
        <circle cx="296" cy="595" r="15" fill={AMBER}/>
        <path d="M353 648 Q393 631 424 671 L469 733 Q411 771 335 745Z" fill={NAVY}/>
        <path d="M336 742 Q402 762 466 733" stroke={AMBER} strokeWidth="10" fill="none"/>
        <path d="M361 754 L593 1107 L173 1169 L337 751Z" fill={AMBER} opacity=".10"/>
      </g>
      <ellipse cx="464" cy="1118" rx="428" ry="378" fill={'url(#'+id+'-glow)'}/>
      <path d="M-83 985 Q429 828 1164 1058 L1300 2400 H-220Z" fill={'url(#'+id+'-wood)'}/>
      <path d="M-30 1217 Q485 1058 1157 1285 M-50 1434 Q507 1359 1105 1514 M44 1042 Q379 949 696 1033 M660 1626 Q884 1638 1120 1570" stroke={MINT} strokeWidth="2" opacity=".10" fill="none"/>
      {painted?<WBPaintedMaterials id={id} surface="wood"/>:null}
      {(/\b(?:account|balance)\b/i.test(scene.narration) || stage==='separate' || stage==='review')?<AccountPhone id={id} painted={painted}/>:null}
      <g data-wb-layer="plant">
        <ellipse cx="112" cy="1068" rx="87" ry="27" fill={NAVY} opacity=".3"/>
        <path d="M51 941 L170 946 L151 1054 Q109 1074 72 1056Z" fill="#CA8466"/>
        <path d="M114 947 Q77 809 110 731 M114 947 Q150 844 171 795" stroke="#5DAB99" strokeWidth="8" fill="none"/>
        <path d="M106 871 Q33 864 25 803 Q88 792 106 871 M106 789 Q70 743 101 686 Q144 730 106 789 M134 862 Q133 793 195 782 Q209 847 134 862" fill={MINT}/>
        <path d="M80 957 Q12 935 19 884 Q72 892 80 957 M153 943 Q183 877 231 896 Q216 949 153 943" fill={TEAL}/>
        <path d="M42 819 L105 867 M105 710 L107 785 M185 798 L139 857" stroke="#457E7A" strokeWidth="3" fill="none"/>
      </g>
      <ellipse cx="594" cy="1442" rx="407" ry="118" fill={NAVY} opacity=".32"/>
      <g data-wb-object="single-open-planning-notebook">
        <path d="M164 1077 L500 951 Q574 944 619 972 L949 1115 L882 1492 Q684 1555 525 1475 L177 1336Z" fill={NAVY}/>
        <path d="M184 1082 L512 974 L521 1445 L191 1324Z" fill="#CBB897"/>
        <path d="M521 1445 L886 1464 L938 1120 L579 978Z" fill="#DDCAA8"/>
        <path d="M177 1058 L475 963 Q536 951 550 990 L521 1418 Q350 1311 168 1300Z" fill={'url(#'+id+'-paper)'}/>
        <path d="M550 990 Q581 954 643 989 L952 1093 L880 1439 Q693 1369 521 1418Z" fill={'url(#'+id+'-paper)'}/>
        <path d="M550 990 L521 1418" stroke="#B7A88B" strokeWidth="5" fill="none"/>
        <path d="M527 990 Q507 1127 504 1395 L522 1418 L550 990Z" fill="#C9B68F" opacity=".18"/>
        <path d="M581 996 Q609 1011 918 1105 L870 1417" fill="none" stroke="#FFFDF3" strokeWidth="5" opacity=".75"/>
        <path d="M189 1071 Q361 1011 467 978 M185 1287 Q325 1299 491 1388" fill="none" stroke="#B4A380" strokeWidth="2" opacity=".34"/>
        <path d="M234 1274 Q359 1278 444 1318 M632 1305 Q743 1326 837 1389" fill="none" stroke={TEAL} strokeWidth="1" opacity=".09"/>
        <path d="M171 1311 Q359 1321 521 1433 Q701 1380 877 1452" stroke={CREAM} strokeWidth="3" fill="none"/>
        {painted?<WBPaintedMaterials id={id} surface="paper"/>:null}
        <g data-wb-purpose="reserve" opacity={stage==='identify'&&!sharedClaim?reveal(left):1}>
          <path d="M175 1088 L130 1105 L129 1170 L168 1161Z" fill={TEAL}/>
          <g transform="rotate(-9 355 1151)">
            <path data-wb-cue="reserve" d="M232 1240 H461" stroke={TEAL} strokeWidth="10" strokeLinecap="round" opacity={stage==='separate'?.18:1}/>
            {stage==='separate'?<path d="M232 1240 H461" stroke={TEAL} strokeWidth="10" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1-leftStroke}/>:null}
          </g>
        </g>
        <g data-wb-purpose="goal" opacity={stage==='identify'&&!sharedClaim?reveal(right):1}>
          <path d="M942 1117 L983 1134 L970 1197 L932 1174Z" fill={AMBER}/>
          <g transform="rotate(8 725 1181)">
            <path data-wb-cue="goal" d="M610 1248 H837" stroke={AMBER} strokeWidth="10" strokeLinecap="round" opacity={stage==='separate'?.18:reviewFocus==='shared-balance'?1-.65*consequence.lessAvailable:1}/>
            {stage==='separate'?<path d="M610 1248 H837" stroke={AMBER} strokeWidth="10" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1-rightStroke}/>:null}
          </g>
        </g>
        <g data-wb-action="compare-purposes" opacity={inspect} fill="none" stroke={CORAL} strokeWidth="5">
          <path d="M222 1254 Q349 1285 458 1247 M600 1265 Q737 1310 851 1263"/>
        </g>
      </g>
      </g>
      <g data-wb-purpose-text="reserve" opacity={stage==='identify'&&!sharedClaim?reveal(left):1} transform="rotate(-9 355 1151)">
        <MeasuredSvgText text="EMERGENCY RESERVE" x={355} y={1130} width={295} maxSize={42} minSize={34} maxLines={2} fill={NAVY}/>
      </g>
      <g data-wb-purpose-text="goal" opacity={stage==='identify'&&!sharedClaim?reveal(right):1} transform="rotate(8 725 1181)">
        <MeasuredSvgText text="FUTURE GOAL" x={725} y={1170} width={280} maxSize={44} minSize={34} maxLines={2} fill={NAVY}/>
      </g>
      {stage==='review' && reviewFocus==='commitments'?<g data-wb-action="inspect-existing-assignments" opacity={reviewBuild}>
        <g transform="rotate(-9 355 1151)"><MeasuredSvgText text="ALREADY ASSIGNED?" x={345} y={1294} width={240} maxSize={28} minSize={24} maxLines={2} fill={INDIGO}/></g>
        <g transform="rotate(8 725 1181)"><MeasuredSvgText text="ALREADY ASSIGNED?" x={720} y={1303} width={250} maxSize={28} minSize={24} maxLines={2} fill={INDIGO}/></g>
      </g>:null}
      {sharedClaim?<g data-wb-action="one-balance-two-claims">
        <path data-wb-connection="reserve" d={`M529 1433 Q360 1396 ${purposeAnchors.reserve.x} ${purposeAnchors.reserve.y}`} fill="none" stroke={CORAL} strokeWidth="6" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1-sharedConnections.reserve}/>
        <path data-wb-connection="goal" d={`M552 1433 Q730 1405 ${purposeAnchors.goal.x} ${purposeAnchors.goal.y}`} fill="none" stroke={CORAL} strokeWidth="6" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1-sharedConnections.goal}/>
        <g data-wb-shared-origin="one-portion" opacity={doublePromiseHook(scene)?1:sharedConnections.reserve}>
          <circle cx="540" cy="1437" r="12" fill={CORAL}/>
          <MeasuredSvgText text="SAME PORTION" x={540} y={1481} width={370} maxSize={36} minSize={30} maxLines={1} fill={CREAM}/>
        </g>
        <g data-wb-action="emergency-claim" opacity={consequence.emergencyClaim}>
          <path d="M220 1185 Q328 1157 475 1189 L468 1255 Q351 1281 220 1242Z" fill="none" stroke={CORAL} strokeWidth="5"/>
        </g>
        <g data-wb-action="less-available-for-goal" opacity={consequence.lessAvailable}>
          <path d="M603 1259 L850 1235" fill="none" stroke={CORAL} strokeWidth="6" strokeLinecap="round"/>
          <g transform="rotate(8 729 1340)"><MeasuredSvgText text="LESS AVAILABLE" x={729} y={1340} width={275} maxSize={32} minSize={28} maxLines={1} fill={NAVY}/></g>
        </g>
      </g>:null}
      {stage==='review' && reviewFocus==='distinction'?<g data-wb-action="compare-distinct-purpose-marks" opacity={reviewBuild}>
        <path d="M235 1242 L460 1208 M609 1232 L837 1264" fill="none" stroke={CORAL} strokeWidth="5" strokeLinecap="round"/>
        <path d="M537 1038 Q526 1188 527 1378" fill="none" stroke={CORAL} strokeWidth="5" strokeDasharray="8 14" opacity=".65"/>
      </g>:null}
      {stage==='review' && reviewFocus==='consequence'?<g data-wb-action="revisit-competing-goal" opacity={reviewBuild}>
        <path d="M604 1277 Q708 1339 854 1290" fill="none" stroke={CORAL} strokeWidth="6" strokeLinecap="round"/>
        <g data-wb-action="review-goal-date" opacity={consequence.reviewDate} transform="rotate(8 729 1352)">
          <MeasuredSvgText text="REVIEW GOAL DATE" x={729} y={1334} width={278} maxSize={29} minSize={25} maxLines={1} fill={INDIGO}/>
          <path d="M633 1370 H808" fill="none" stroke={TEAL} strokeWidth="4" strokeLinecap="round"/>
          <circle cx="640" cy="1370" r="6" fill={TEAL}/>
          <circle cx="738" cy="1370" r="6" fill={TEAL}/>
          <path d="M797 1355 Q812 1343 825 1355 Q834 1368 817 1373 V1381 M817 1392 H818" fill="none" stroke={CORAL} strokeWidth="5" strokeLinecap="round"/>
        </g>
      </g>:null}
      <g filter={painted?'url(#'+id+'-brush-edge)':undefined}><WritingHand x={handX} y={handY-15} opacity={handOpacity} painted={painted} id={id}/></g>
      </g>
    </svg>
    <div style={{position:'absolute',inset:'0 0 auto',height:300,background:'linear-gradient(to bottom,rgba(23,26,59,.78),rgba(23,26,59,.35) 70%,transparent)',pointerEvents:'none'}}/>
    <div data-wb-chrome="minimal" style={{position:'absolute',left:72,right:112,top:144,color:CREAM}}>
      <MeasuredText text={headline} width={896} maxSize={58} minSize={46} maxLines={2}/>
    </div>
  </SceneShell>;
};
