import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect,vi} from 'vitest';
let frame=0;
vi.mock('remotion',async original=>({...await original<typeof import('remotion')>(),useCurrentFrame:()=>frame}));
import {editorialClaimsDirection,editorialClaimsState,editorialPlanningDirection,editorialPlanningState,editorialNamingLayout} from '../src/EditorialPlanningScene';
import recorded from './fixtures/planning-story-directed.json';
import {EditorialMotionScene} from '../src/EditorialMotionScene';
import {DEFAULT_BRAND,RENDERER_VERSION,validateManifest,type ManifestScene} from '../src/contracts';

const fixture=validateManifest({schema_version:'1.0',manifest_id:'fixture',job_id:'fixture',project_id:'fixture',
  channel:'wealth_blueprint',language_code:'en',brand_profile_version:'test',renderer_version:RENDERER_VERSION,
  content_hash:'0'.repeat(64),scenes:recorded});
const story=(index:number):ManifestScene=>structuredClone(fixture.scenes[index]);
const scene=()=>story(0);

describe('directed qualitative claims in the ordinary editorial composition',()=>{
  it('starts meaningful changes before promised, then draws each claim at its actual phrase',()=>{
    const s=scene();
    expect(editorialClaimsState(s,20).notSpent).toBe(0);
    expect(editorialClaimsState(s,40).notSpent).toBe(1);
    expect(editorialClaimsState(s,60).portion).toBe(1);
    expect(editorialClaimsState(s,98).firstClaim).toBe(0);
    expect(editorialClaimsState(s,116)).toMatchObject({firstClaim:expect.any(Number),secondClaim:0,conflict:0});
    expect(editorialClaimsState(s,116).firstClaim).toBeGreaterThan(.9);
    expect(editorialClaimsState(s,135).secondClaim).toBeGreaterThan(.9);
    expect(editorialClaimsState(s,150)).toMatchObject({firstClaim:1,secondClaim:1,conflict:1,hold:true});
    expect(editorialClaimsState(s,150)).toEqual(editorialClaimsState(s,170));
  });
  it('uses scene-relative final audio times rather than restarting cues at each cut',()=>{
    const s=scene(),offset=11.2;
    s.timing={start_sec:offset,end_sec:offset+5.883};
    s.caption_words=s.caption_words!.map(w=>({...w,start_sec:w.start_sec+offset,end_sec:w.end_sec+offset}));
    expect(editorialClaimsDirection(s).firstAt).toBeCloseTo(3.282,8);
    expect(editorialClaimsState(s,150)).toEqual(editorialClaimsState(scene(),150));
  });
  it('rejects the old mismatched expense instead of drawing a superficially valid claims diagram',()=>{
    const s=scene();s.visual_relation!.objects=[{role:'expense',label:'UNEXPECTED NEED'},{role:'reserve',label:'READY RESERVE'}];
    expect(()=>editorialClaimsDirection(s)).toThrow('objects must describe');
  });
  it('rejects missing, contradictory or too-late timing rather than inventing a movement',()=>{
    const noWords=scene();noWords.caption_words=[];expect(()=>editorialClaimsDirection(noWords)).toThrow('audio-bound');
    const noCue=scene();noCue.caption_words=noCue.caption_words!.filter(w=>w.text!=='twice');
    expect(()=>editorialClaimsDirection(noCue)).toThrow('actual spoken');
    const overlap=scene();overlap.caption_words![2].start_sec=.4;
    expect(()=>editorialClaimsDirection(overlap)).toThrow('monotonic');
    const rushed=scene();rushed.timing.end_sec=5.1;rushed.caption_words=rushed.caption_words!.filter(w=>w.end_sec<5.1);
    expect(()=>editorialClaimsDirection(rushed)).toThrow('reading hold');
  });
  it('routes through the existing EditorialMotionScene with one source and plain captions, without fabricated chart data',()=>{
    // Frame 110 is a real alignment gap between promised and them; use spoken frame 109.
    frame=109;
    const html=renderToStaticMarkup(<EditorialMotionScene scene={scene()} brand={{...DEFAULT_BRAND,visualVariant:'editorial_motion'}} subjects={[]}/>);
    expect(html.match(/data-editorial-source="one-portion"/g)).toHaveLength(1);
    expect(html).toContain('data-editorial-planning="shared-balance"');
    expect(html).toContain('data-caption-style="plain_phrase"');
    expect(html).not.toContain('data-verified-curve');expect(html).not.toContain('data-editorial-mode="question"');
    expect(html).not.toContain('banknote');expect(html).not.toContain('transfer-arrow');
  });
  it('does not silently turn other reserve actions into the same opening diagram',()=>{
    const s=scene();s.visual_relation!.planning_desk_action='write-purposes';s.visual_relation!.stage='separate';
    expect(()=>renderToStaticMarkup(<EditorialMotionScene scene={s} brand={DEFAULT_BRAND} subjects={[]}/>)).toThrow('stale audio-bound');
  });
  it('does not omit a narrated conditional consequence or pretend a numeric claim is a qualitative diagram',()=>{
    const conditional=scene();conditional.narration+=' If an emergency occurs, less is available for the goal.';
    expect(()=>editorialClaimsDirection(conditional)).toThrow('Conditional emergency');
    const numeric=scene();numeric.narration+=' Consider $100.';
    expect(()=>editorialClaimsDirection(numeric)).toThrow('qualitative');
  });
});

describe('recorded planning story uses distinct audio-bound actions',()=>{
  it('keeps the two purpose cards and the retired source apart throughout the transition, including intermediate frames',()=>{
    for(let i=0;i<=100;i++){
      const {left,right,retired,page}=editorialNamingLayout(i/100);
      const overlapX=left.x<right.x+right.w&&right.x<left.x+left.w;
      const overlapY=left.y<right.y+278&&right.y<left.y+278;
      expect(overlapX&&overlapY).toBe(false);
      if(left.y<764)expect(retired).toBe(1);
      if(page>0)expect(retired).toBe(1);
      for(const card of [left,right])expect(card.x+card.w).toBeLessThanOrEqual(940);
    }
  });
  it.each(['planning_claims','purpose_naming','portion_audit','conditional_availability','goal_review'])('routes the actual scene to %s',(mode)=>{
    const index=['planning_claims','purpose_naming','portion_audit','conditional_availability','goal_review'].indexOf(mode);
    const s=story(index);frame=Math.floor((s.timing.end_sec-s.timing.start_sec-.1)*30);
    expect(editorialPlanningDirection(s).mode).toBe(mode);
    const html=renderToStaticMarkup(<EditorialMotionScene scene={s} brand={DEFAULT_BRAND} subjects={[]}/>);
    expect(html).toContain('data-editorial-planning=');expect(html).not.toContain('data-verified-curve');
    if(mode==='purpose_naming')expect(html).toContain('one-planning-page');
    // MeasuredText waits for the actual font in the browser; SSR checks only geometry/semantics.
    if(mode==='portion_audit')expect(html).toContain('Inspecting one portion against two possible claims');
    if(mode==='conditional_availability')expect(html).toContain('Conditional reserve use leaves less for the other purpose');
    if(mode==='goal_review'){expect(html).toContain('undated-question');expect(html).not.toMatch(/202\d|JUNE|JULY|MONTHS/);}
  });
  it('retains the source and two purpose cards while the page opens, then marks each at its spoken cue',()=>{
    const s=story(1),state=(sec:number)=>editorialPlanningState(s,Math.floor(sec*30));
    expect(state(0).open).toBe(0);expect(state(.7).open).toBe(1);
    expect(state(2.4).reserve).toBe(0);expect(state(3.1).reserve).toBe(1);
    expect(state(5.2).goal).toBe(0);expect(state(6).goal).toBe(1);
    expect(state(7)).toEqual(state(7.2));
  });
  it('reveals questions about both claims without treating them as a verified account result',()=>{
    const s=story(2);expect(editorialPlanningState(s,60).portion).toBe(0);
    expect(editorialPlanningState(s,120).counted).toBeGreaterThan(.9);
    expect(editorialPlanningState(s,120).both).toBe(0);
    expect(editorialPlanningState(s,150).both).toBe(1);
  });
  it('does not reveal the reduced availability at the early word reduces before the actual if/emergency condition',()=>{
    const s=story(3),d=editorialPlanningDirection(s);
    expect(d.cue_times.condition).toBeCloseTo(5.843,3);
    expect(d.cue_times.effect).toBeGreaterThan(d.cue_times.condition);
    expect(editorialPlanningState(s,140).effect).toBe(0);
    expect(editorialPlanningState(s,186).condition).toBeGreaterThan(.8);
    expect(editorialPlanningState(s,186).effect).toBe(0);
    expect(editorialPlanningState(s,210).effect).toBe(1);
  });
  it('version 2 leads attention through the actual phrases before changing availability',()=>{
    const s=story(3),d=s.visual_relation!.editorial_direction!;
    d.version=2;
    const actual=(tokens:string[])=>s.caption_words!.find(w=>tokens.includes(w.text.toLowerCase().replace(/[^a-z]/g,'')))!.start_sec-s.timing.start_sec;
    d.cue_times!.source_context=actual(['same','funds','portion']);
    d.cue_times!.claim_context=actual(['two','competing','claims']);
    const before=editorialPlanningState(s,0);
    const source=editorialPlanningState(s,Math.round((d.cue_times!.source_context+.6)*30));
    const claims=editorialPlanningState(s,Math.round((d.cue_times!.claim_context+.6)*30));
    expect(before.sourceAttention).toBe(0);
    expect(source.sourceAttention).toBeGreaterThan(0);
    expect(claims.claimAttention).toBeGreaterThan(.9);
    expect(source.effect).toBe(0);expect(claims.effect).toBe(0);
    expect(claims.condition).toBe(0);
    d.cue_times!.claim_context=.1;
    expect(()=>editorialPlanningDirection(s)).toThrow('Stale');
  });
  it.each(['missing','stale','expense','number','condition','hash'])('rejects %s instead of a plausible generic image',(mutation)=>{
    const s=story(3);
    if(mutation==='missing')delete s.visual_relation!.editorial_direction;
    if(mutation==='stale')s.visual_relation!.editorial_direction!.cue_times!.effect=1;
    if(mutation==='expense')s.visual_relation!.objects!.push({role:'expense',label:'CAR REPAIR'});
    if(mutation==='number')s.narration+=' $100.';
    if(mutation==='condition')s.visual_relation!.condition=null;
    if(mutation==='hash')s.visual_relation!.editorial_direction!.narration_hash='z'.repeat(64);
    expect(()=>editorialPlanningDirection(s)).toThrow();
  });
});
