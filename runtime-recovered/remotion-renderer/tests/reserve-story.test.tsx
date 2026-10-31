import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it,vi} from 'vitest';
let currentFrame=0;
vi.mock('remotion',async original=>({...await original<typeof import('remotion')>(),useCurrentFrame:()=>currentFrame}));
import {ReserveReadinessScene, educationalQuestion} from '../src/ReserveReadinessScene';
import {DEFAULT_BRAND,type ManifestScene} from '../src/contracts';

const scene=(stage:string,label='CAR REPAIR'):ManifestScene=>({scene_id:'isolated',scene_type:'HookScene',
  narration:'Could you cover the repair today?',caption:'',assets:[],headline:'Could you cover it today?',
  timing:{start_sec:0,end_sec:7},caption_words:[],visual_relation:{kind:'reserve_readiness',stage,objects:[{role:'expense',label}]}});

describe('reserve story preserves meaning through actual composition',()=>{
  it('educational hook shows the situation and curiosity without duplicate slogan cards',()=>{
    currentFrame=0;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('need')} brand={{...DEFAULT_BRAND,presentation:'educational_illustration'}}/>);
    // MeasuredText intentionally defers browser font measurement; don't pretend SSR displays it.
    expect(educationalQuestion('need')).toBe('Your car breaks down. Now what?');
    expect(html).toContain('car-repair');
    expect(html).not.toContain('COULD YOU COVER IT TODAY?');
    expect(html).not.toContain('WHEN LIFE INTERRUPTS');
  });
  it('educational access preserves all three genuine unanswered checks',()=>{
    currentFrame=180;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('access')} brand={{...DEFAULT_BRAND,presentation:'educational_illustration'}}/>);
    expect(educationalQuestion('access')).toBe('Can you actually access it?');
    for(const text of ['WHERE IS IT HELD?','HOW DO YOU ACCESS IT?','WHAT CONDITIONS APPLY?']) expect(html).toContain(text);
    expect(html).not.toContain('CHECK, DON');
    expect(html).not.toContain('AVAILABLE NOW');
  });
  it('shows a concrete car at frame zero, without waiting through a blank hook',()=>{
    currentFrame=0;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('need')} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('data-wb-object="car-repair"');
    expect(html).toContain('COULD YOU COVER IT TODAY?');
    expect(html).not.toContain('future-goal-notebook');
  });
  it('does not inject a car into a medical scene',()=>{
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('need','MEDICAL BILL')} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('MEDICAL BILL');
    expect(html).not.toContain('data-wb-object="car-repair"');
  });
  it('shows distinct reserve and future-goal objects with no completed transfer or quantified returns',()=>{
    currentFrame=90;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('separate')} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('reserve-envelope');
    expect(html).toContain('future-goal-notebook');
    expect(html).toContain('LABEL EACH PURPOSE CLEARLY');
    expect(html.replace(/<[^>]*>/g,'')).not.toMatch(/\$\d|[0-9]+%|TRANSFERRED|GUARANTEED/);
  });
  it('shows questions, not a fake successful access receipt',()=>{
    currentFrame=90;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('access')} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('WHAT CONDITIONS APPLY?');
    expect(html).toContain('CHECK THE TERMS');
    expect(html).not.toMatch(/TRANSFERRED|AVAILABLE NOW|WITHOUT FEES/);
  });
  it('explains conditional terms in a distinct close-up without claiming a penalty applies',()=>{
    currentFrame=150;
    const terms={...scene('access'),visual_relation:{...scene('access').visual_relation,access_focus:'terms' as const}};
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={terms} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('own-terms-close-up');
    expect(html).toContain('WAITING TIME?');
    expect(html).toContain('ANY CHARGES?');
    expect(html).toContain('IF YOUR TERMS REQUIRE IT');
    expect(html).not.toContain('WHERE IS IT HELD?');
    expect(html).not.toMatch(/TRANSFERRED|AVAILABLE NOW|WITHOUT FEES/);
  });
  it('names the expense in a close-up while keeping the nearby reserve object',()=>{
    currentFrame=180;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('identify')} brand={DEFAULT_BRAND}/>);
    expect(html).toContain('data-wb-shot="expense-close-up"');
    expect(html).toContain('data-wb-object="essential-expense"');
    expect(html).toContain('data-wb-object="reserve-envelope"');
    expect(html).toContain('READY RESERVE');
  });
  it('visually inspects both roles instead of repeating the unaltered comparison',()=>{
    currentFrame=140;
    const review=renderToStaticMarkup(<ReserveReadinessScene scene={scene('review')} brand={DEFAULT_BRAND}/>);
    const comparison=renderToStaticMarkup(<ReserveReadinessScene scene={scene('separate')} brand={DEFAULT_BRAND}/>);
    expect(review).toContain('data-wb-action="inspect-both-roles"');
    expect(comparison).not.toContain('inspect-both-roles');
    expect(review).toContain('reserve-envelope');
    expect(review).toContain('future-goal-notebook');
    expect(review).not.toMatch(/TRANSFERRED|GUARANTEED|AVAILABLE NOW/);
  });
  it('retains the already-introduced future goal in the very first review frame',()=>{
    currentFrame=0;
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('review')} brand={DEFAULT_BRAND}/>);
    expect(html).toMatch(/<g opacity="1"[^>]*><g data-wb-object="future-goal-notebook"/);
    expect(html).toMatch(/<g opacity="1"><text[^>]*>FUTURE GOAL<\/text>/);
  });
  it('holds the lens on the reserve before the spoken change, then reaches the goal',()=>{
    const timed={...scene('review'),timing:{start_sec:27.233,end_sec:35.797},caption_words:[
      {text:'Review',start_sec:27.233,end_sec:27.513},
      {text:'circumstances',start_sec:29.534,end_sec:30.255},
      {text:'change',start_sec:30.355,end_sec:30.655},
    ]} as ManifestScene;
    const render=(frame:number)=>{currentFrame=frame;return renderToStaticMarkup(<ReserveReadinessScene scene={timed} brand={{...DEFAULT_BRAND,presentation:'educational_illustration'}}/>);};
    expect(render(30)).toContain('translate(238 330)');
    const during=render(73).match(/data-wb-action="inspect-both-roles"[^>]*translate\(([^ ]+) 330\)/);
    expect(Number(during?.[1])).toBeGreaterThan(238);
    expect(Number(during?.[1])).toBeLessThan(708);
    expect(render(130)).toContain('translate(708 330)');
  });
});
