import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect,vi} from 'vitest';
let currentFrame=0;
vi.mock('remotion',async original=>({...await original<typeof import('remotion')>(),useCurrentFrame:()=>currentFrame}));
import {WBPlanningDeskScene,supportsPlanningDesk,planningDeskCamera,planningDeskReviewFocus,planningDeskHeadline,planningDeskConsequenceState,planningDeskSharedConnections,planningDeskPurposeAnchors} from '../src/WBPlanningDeskScene';
import {ReserveReadinessScene} from '../src/ReserveReadinessScene';
import {WBPaintedDefinitions,WBPaintedMaterials} from '../src/WBPaintedMaterials';
import {DEFAULT_BRAND,type ManifestScene} from '../src/contracts';
const scene=(stage='separate'):ManifestScene=>({scene_id:'desk',scene_type:'HookScene',timing:{start_sec:0,end_sec:8},
  headline:'Two purposes. One balance.',narration:'Name your reserve separately from a future goal.',caption:'',assets:[],
  caption_words:[{text:'reserve',start_sec:1,end_sec:1.4},{text:'future',start_sec:3,end_sec:3.4}],
  visual_relation:{kind:'reserve_readiness',stage,objects:[{role:'reserve',label:'READY RESERVE'},{role:'goal',label:'FUTURE GOAL'}]}});
describe('count-controlled original WB planning illustration',()=>{
  it('opens the recorded double-promise hook on the actual conflict, with both purposes already legible',()=>{
    const hook={...scene('identify'),narration:'You have not spent your savings, but you may have promised them twice to different goals today.',
      caption_words:[{text:'promised',start_sec:3,end_sec:3.4},{text:'twice',start_sec:3.8,end_sec:4.1}]};
    expect(planningDeskCamera(hook,0)).toMatchObject({shot:'shared-portion-close-up',scale:1.4});
    expect(planningDeskHeadline(hook)).toBe('Promised twice?');
    currentFrame=0;
    const opening=renderToStaticMarkup(<WBPlanningDeskScene scene={hook} brand={DEFAULT_BRAND}/>);
    expect(opening).toContain('data-wb-planned-action="shared-balance"');
    expect(opening).toContain('data-wb-purpose="reserve" opacity="1"');
    expect(opening).toContain('data-wb-purpose="goal" opacity="1"');
    expect(opening).toContain('data-wb-action="one-balance-two-claims"');
    expect(opening).toContain('data-wb-shared-origin="one-portion" opacity="1"');
    expect(planningDeskSharedConnections(hook,89)).toEqual({reserve:0,goal:0});
    expect(planningDeskSharedConnections(hook,108)).toEqual({reserve:1,goal:0});
    expect(planningDeskSharedConnections(hook,140)).toEqual({reserve:1,goal:1});
    expect(planningDeskConsequenceState(hook,150)).toMatchObject({emergencyClaim:0,lessAvailable:0});
  });
  it('does not invent a double promise when the hook only asks to inspect twice',()=>{
    const hook={...scene('identify'),narration:'Look twice at your savings before deciding.'};
    currentFrame=0;
    const opening=renderToStaticMarkup(<WBPlanningDeskScene scene={hook} brand={DEFAULT_BRAND}/>);
    expect(opening).not.toContain('data-wb-action="one-balance-two-claims"');
  });
  it('connects the shared portion to the rotated purpose marks instead of floating below them',()=>{
    const anchors=planningDeskPurposeAnchors();
    // The underline centres use the page rotations, not unrotated page coordinates.
    expect(anchors.reserve.x).toBeCloseTo(368.9227,3);
    expect(anchors.reserve.y).toBeCloseTo(1238.9043,3);
    expect(anchors.goal.x).toBeCloseTo(715.6754,3);
    expect(anchors.goal.y).toBeCloseTo(1247.3480,3);
    const shot={...scene('review'),narration:'Inspect the same funds counted for both purposes.'};
    currentFrame=150;
    const html=renderToStaticMarkup(<WBPlanningDeskScene scene={shot} brand={DEFAULT_BRAND}/>);
    expect(html).toContain(`Q360 1396 ${anchors.reserve.x} ${anchors.reserve.y}`);
    expect(html).toContain(`Q730 1405 ${anchors.goal.x} ${anchors.goal.y}`);
  });
  it('keeps exactly one planning notebook and no banknotes or cash arrows',()=>{
    currentFrame=120; const html=renderToStaticMarkup(<WBPlanningDeskScene scene={scene()} brand={DEFAULT_BRAND}/>);
    expect(html.match(/data-wb-object="single-open-planning-notebook"/g)).toHaveLength(1);
    expect(html).toContain('EMERGENCY RESERVE');expect(html).toContain('FUTURE GOAL');
    expect(html).not.toContain('banknote');expect(html).not.toContain('transfer-arrow');
  });
  it('introduces purpose labels at their real speech cues',()=>{
    currentFrame=0;const early=renderToStaticMarkup(<WBPlanningDeskScene scene={scene('identify')} brand={DEFAULT_BRAND}/>);
    expect(early).toContain('data-wb-purpose="goal" opacity="0"');
    currentFrame=110;const later=renderToStaticMarkup(<WBPlanningDeskScene scene={scene('identify')} brand={DEFAULT_BRAND}/>);
    expect(later).toContain('data-wb-purpose="goal" opacity="1"');
  });
  it('preserves established purpose labels across the next shot while emphasizing speech cues',()=>{
    currentFrame=0;const html=renderToStaticMarkup(<WBPlanningDeskScene scene={scene()} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('data-wb-purpose="goal" opacity="1"');
    expect(html).toContain('data-wb-cue="goal"');
    expect(html).toContain('opacity="0.18"');
  });
  it('requires explicit opt-in and keeps existing diagrams unchanged',()=>{
    const old=renderToStaticMarkup(<ReserveReadinessScene scene={scene()} brand={DEFAULT_BRAND}/>);
    expect(old).not.toContain('planning-desk-candidate-v1');
    const candidate=renderToStaticMarkup(<ReserveReadinessScene scene={scene()} brand={{...DEFAULT_BRAND,illustrationSystem:'wb_planning_desk_v1'}}/>);
    expect(candidate).toContain('planning-desk-candidate-v1');
  });
  it('does not silently substitute this illustration for a car emergency or annual bill',()=>{
    expect(supportsPlanningDesk(scene('need'))).toBe(false);
    expect(supportsPlanningDesk({...scene(),visual_relation:{...scene().visual_relation,objects:[{role:'goal',label:'PLANNED BILL FUND'}]}})).toBe(false);
    expect(()=>renderToStaticMarkup(<ReserveReadinessScene scene={scene('need')} brand={{...DEFAULT_BRAND,illustrationSystem:'wb_planning_desk_v1'}}/>)).toThrow('Unsupported planning-desk');
  });
  it('changes from one account to the two-purpose context at the actual But cue, then holds',()=>{
    const shot={...scene('identify'),narration:'One account. But money has two jobs.',
      caption_words:[{text:'But',start_sec:2.7,end_sec:2.9}]};
    expect(planningDeskCamera(shot,80)).toMatchObject({scale:2.05,x:865,y:716});
    const moving=planningDeskCamera(shot,91);
    expect(moving.scale).toBeGreaterThan(1);expect(moving.scale).toBeLessThan(2.05);
    expect(planningDeskCamera(shot,101)).toEqual(planningDeskCamera(shot,150));
    expect(planningDeskCamera(shot,150).scale).toBeCloseTo(1,10);
    expect(planningDeskCamera(shot,150).shot).toBe('two-purposes-context');
  });
  it('moves closer for notation at the actual Open cue and holds the final reading position',()=>{
    const shot={...scene(),timing:{start_sec:6,end_sec:14},
      caption_words:[{text:'Open',start_sec:6.5,end_sec:6.7}]};
    expect(planningDeskCamera(shot,14)).toMatchObject({scale:1,y:960});
    expect(planningDeskCamera(shot,35)).toEqual(planningDeskCamera(shot,150));
    expect(planningDeskCamera(shot,150)).toMatchObject({scale:1.22,y:1180,shot:'notation-close-up'});
  });
  it('keeps one phone and one notebook while the functional pen follows purpose notation',()=>{
    const shot={...scene(),caption_words:[{text:'name',start_sec:.8,end_sec:1},
      {text:'emergency',start_sec:1.5,end_sec:2},{text:'planned',start_sec:3.5,end_sec:4}]};
    currentFrame=60;const left=renderToStaticMarkup(<WBPlanningDeskScene scene={shot} brand={DEFAULT_BRAND}/>);
    currentFrame=120;const right=renderToStaticMarkup(<WBPlanningDeskScene scene={shot} brand={DEFAULT_BRAND}/>);
    expect(left.match(/data-wb-object="single-account-phone"/g)).toHaveLength(1);
    expect(left.match(/data-wb-object="single-open-planning-notebook"/g)).toHaveLength(1);
    const penTransform=(html:string)=>html.match(/data-wb-action="write-purpose-underline" opacity="1" transform="([^"]+)"/)?.[1];
    expect(penTransform(left)).toBeDefined();expect(penTransform(right)).toBeDefined();
    expect(penTransform(left)).not.toEqual(penTransform(right));
    expect(right).toContain('stroke-dashoffset="0"');
  });
  it('offers painted materials by explicit opt-in without adding objects or distorting purpose text',()=>{
    currentFrame=120;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene()} brand={{...DEFAULT_BRAND,illustrationSystem:'wb_planning_desk_painted_v1'}}/>);
    expect(html.match(/data-wb-object="single-open-planning-notebook"/g)).toHaveLength(1);
    expect(html).toContain('data-wb-material="fixed-gouache-pigments"');
    expect(html).toContain('data-wb-purpose-text="reserve"');
    expect(html).toContain('data-wb-purpose-text="goal"');
    const clean=renderToStaticMarkup(<ReserveReadinessScene scene={scene()} brand={{...DEFAULT_BRAND,illustrationSystem:'wb_planning_desk_v1'}}/>);
    expect(clean).not.toContain('fixed-gouache-pigments');
  });
  it('keeps pigment patterns fixed across all frames instead of regenerating grain or brush marks',()=>{
    const materials=()=>renderToStaticMarkup(<svg><defs><WBPaintedDefinitions id="fixed"/></defs><WBPaintedMaterials id="fixed"/></svg>);
    currentFrame=0;const first=materials();currentFrame=239;expect(materials()).toEqual(first);
    expect(first).toContain('seed="17"');expect(first).toContain('seed="53"');
    expect(first).toContain('seed="91"');
    expect(first).not.toContain('<animate');
  });
  it('keeps background and desk pigments behind the phone and notebook instead of painting through them',()=>{
    currentFrame=120;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene()} brand={{...DEFAULT_BRAND,illustrationSystem:'wb_planning_desk_painted_v1'}}/>);
    const surface=(name:string)=>html.indexOf(`data-wb-surface="${name}"`);
    const phone=html.indexOf('data-wb-object="single-account-phone"');
    const book=html.indexOf('data-wb-object="single-open-planning-notebook"');
    expect(surface('wall')).toBeLessThan(phone);expect(surface('sky')).toBeLessThan(phone);
    expect(surface('wood')).toBeLessThan(phone);expect(surface('wood')).toBeLessThan(book);
    expect(surface('paper')).toBeGreaterThan(book);
    expect(surface('paper')).toBeLessThan(html.indexOf('data-wb-purpose="reserve"'));
  });
  it('distinguishes existing commitments from shared balance and unclear purposes in real review sentences',()=>{
    expect(planningDeskReviewFocus({...scene('review'),narration:'Check what is already committed.'})).toBe('commitments');
    expect(planningDeskReviewFocus({...scene('review'),narration:'Inspect the balance for double-counting.'})).toBe('shared-balance');
    expect(planningDeskReviewFocus({...scene('review'),narration:'If you assign the same funds to both, review the choices.'})).toBe('shared-balance');
    expect(planningDeskReviewFocus({...scene('review'),narration:'Record which plan changes if the funds are used today.'})).toBe('consequence');
    expect(planningDeskReviewFocus({...scene('review'),narration:'Confusing the distinct roles creates a problem.'})).toBe('distinction');
    currentFrame=120;const html=renderToStaticMarkup(<WBPlanningDeskScene scene={{...scene('review'),narration:'Inspect the balance for double-counting.'}} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('data-wb-action="one-balance-two-claims"');expect(html).toContain('SAME PORTION');
    expect(html).not.toContain('transfer-arrow');expect(html).not.toContain('banknote');
  });
  it('keeps the opening account shot for an actual balance hook, without requiring a bank-app keyword',()=>{
    const hook={...scene('identify'),narration:'Is one balance serving both plans? Check whether the same portion is counted twice.',
      caption_words:[{text:'Check',start_sec:2.5,end_sec:2.7}]};
    expect(planningDeskCamera(hook,60).shot).toBe('one-account-close-up');
    currentFrame=15;const html=renderToStaticMarkup(<WBPlanningDeskScene scene={hook} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('single-account-phone');
    // SSR does not load browser fonts; only LIVE frames prove measured text visibility.
    expect(planningDeskHeadline(hook)).toBe('One balance. Two jobs.');
  });
  it('consumes a declared action and rejects a fabricated different action for the same speech',()=>{
    const declared={...scene('review'),narration:'Inspect the portion already assigned.',
      visual_relation:{...scene('review').visual_relation,planning_desk_action:'existing-assignments' as const}};
    currentFrame=120;const html=renderToStaticMarkup(<WBPlanningDeskScene scene={declared} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('data-wb-planned-action="existing-assignments"');
    expect(html).toContain('data-wb-action="inspect-existing-assignments"');
    expect(supportsPlanningDesk({...declared,visual_relation:{...declared.visual_relation,planning_desk_action:'shared-balance'}})).toBe(false);
  });
  it('shows a qualitative consequence at the recorded reduces cue, without an invented money amount',()=>{
    const shot={...scene('review'),timing:{start_sec:10,end_sec:17},
      narration:'If you assign the same funds to both, an emergency claim reduces money available for the purchase.',
      caption_words:[{text:'emergency',start_sec:11,end_sec:11.3},{text:'reduces',start_sec:12,end_sec:12.3}]};
    expect(planningDeskConsequenceState(shot,59).lessAvailable).toBe(0);
    expect(planningDeskConsequenceState(shot,77).lessAvailable).toBe(1);
    currentFrame=59;const before=renderToStaticMarkup(<WBPlanningDeskScene scene={shot} brand={DEFAULT_BRAND}/>);
    currentFrame=100;const after=renderToStaticMarkup(<WBPlanningDeskScene scene={shot} brand={DEFAULT_BRAND}/>);
    expect(before).toContain('data-wb-action="less-available-for-goal" opacity="0"');
    expect(after).toContain('data-wb-action="less-available-for-goal" opacity="1"');
    expect(after).toContain('LESS AVAILABLE');
    expect(after).toContain('data-wb-purpose="goal" opacity="1"');
    expect(after).not.toMatch(/\$\d|transfer-arrow|banknote/);
  });
  it('directs the final review toward an undated goal timeline at the impact cue',()=>{
    const shot={...scene('review'),narration:'Record which plan changes. Note the impact on your future goal timeline.',
      caption_words:[{text:'impact',start_sec:3,end_sec:3.4}]};
    expect(planningDeskConsequenceState(shot,89).reviewDate).toBe(0);
    expect(planningDeskCamera(shot,89)).toMatchObject({scale:1.22,x:540,shot:'review-both-purposes'});
    expect(planningDeskCamera(shot,130)).toMatchObject({x:565,shot:'review-goal-date'});
    expect(planningDeskCamera(shot,130).scale).toBeCloseTo(1.30,10);
    currentFrame=130;const html=renderToStaticMarkup(<WBPlanningDeskScene scene={shot} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('data-wb-action="review-goal-date" opacity="1"');
    expect(html).toContain('REVIEW GOAL DATE');
    expect(html).not.toMatch(/30 DAYS|DELAYED|\$\d/);
  });
  it('does not invent an emergency or a reduced goal for a double-counting inspection alone',()=>{
    const shot={...scene('review'),narration:'Inspect the single balance to locate any portion currently counted for both purposes.'};
    expect(planningDeskReviewFocus(shot)).toBe('shared-balance');
    expect(planningDeskConsequenceState(shot,240)).toMatchObject({emergencyClaim:0,lessAvailable:0});
    currentFrame=240;
    const html=renderToStaticMarkup(<WBPlanningDeskScene scene={shot} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('data-wb-action="emergency-claim" opacity="0"');
    expect(html).toContain('data-wb-action="less-available-for-goal" opacity="0"');
    expect(html).toContain('SAME PORTION');
  });
  it('waits for a condition spoken after the effect, instead of animating reduced availability too early',()=>{
    const shot={...scene('review'),timing:{start_sec:20,end_sec:28},
      narration:'Assigning the same funds to two competing claims reduces availability if an emergency occurs.',
      caption_words:[{text:'reduces',start_sec:23,end_sec:23.4},{text:'emergency',start_sec:26,end_sec:26.4}]};
    expect(planningDeskConsequenceState(shot,120)).toMatchObject({emergencyClaim:0,lessAvailable:0});
    expect(planningDeskConsequenceState(shot,193).lessAvailable).toBe(0);
    const after=planningDeskConsequenceState(shot,225);
    expect(after.emergencyClaim).toBe(1);expect(after.lessAvailable).toBe(1);
  });
  it('draws two claims at their own speech cues, then holds both without a perpetual movement',()=>{
    const shot={...scene('review'),narration:'The same portion is assigned to both purposes.',
      caption_words:[{text:'same',start_sec:1,end_sec:1.2},{text:'both',start_sec:2,end_sec:2.3}]};
    expect(planningDeskSharedConnections(shot,29)).toEqual({reserve:0,goal:0});
    const first=planningDeskSharedConnections(shot,48);
    expect(first).toEqual({reserve:1,goal:0});
    const second=planningDeskSharedConnections(shot,69);
    expect(second.reserve).toBe(1);expect(second.goal).toBeGreaterThan(0);expect(second.goal).toBeLessThan(1);
    expect(planningDeskSharedConnections(shot,80)).toEqual(planningDeskSharedConnections(shot,200));
    currentFrame=48;
    const html=renderToStaticMarkup(<WBPlanningDeskScene scene={shot} brand={DEFAULT_BRAND}/>);
    expect(html).toMatch(/data-wb-connection="reserve"[^>]*stroke-dashoffset="0"/);
    expect(html).toMatch(/data-wb-connection="goal"[^>]*stroke-dashoffset="1"/);
  });
  it('frames the shared comparison more closely while preserving both full label regions on a phone',()=>{
    const shot={...scene('review'),narration:'Inspect the same funds counted for both purposes.'};
    const camera=planningDeskCamera(shot,0);
    expect(camera.shot).toBe('shared-portion-close-up');
    expect(camera.scale).toBeGreaterThan(1.22);
    for(const [center,width] of [[355,295],[725,280]]){
      const x=540+(center-camera.x)*camera.scale;
      expect(x-width*camera.scale/2).toBeGreaterThanOrEqual(72);
      expect(x+width*camera.scale/2).toBeLessThanOrEqual(1008);
    }
    expect(planningDeskCamera(shot,0)).toEqual(planningDeskCamera(shot,240));
  });
});
