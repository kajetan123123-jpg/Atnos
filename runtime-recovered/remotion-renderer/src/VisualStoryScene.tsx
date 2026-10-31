import React from 'react';
import {Img, useCurrentFrame} from 'remotion';
import type {BrandSpec, ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText} from './typography';

export const smoothProgress = (time: number, start: number, duration: number): number => {
  const t = Math.min(1, Math.max(0, (time-start)/duration));
  return t*t*(3-2*t);
};
const levels = {raise: 0, spending: 1, costs: 2, choice: 3, goal: 4, question: 5} as const;
const level = (state: string | null) => state ? levels[state as keyof typeof levels] : -1;
export const storyState = (state: string) => ({
  spending: level(state) >= 1, recurring: level(state) >= 2,
  saving: level(state) >= 3, goal: level(state) >= 4,
});

export const VisualStoryScene: React.FC<{scene: ManifestScene; brand: BrandSpec; assetData: Record<string,string>}> = ({scene, brand, assetData}) => {
  const frame = useCurrentFrame();
  const story = scene.visual_story!;
  if (!(story.state in levels) || story.timing_source !== 'final_voice_word_alignment') throw new Error('Unsupported or unaligned WB visual action');
  const p = smoothProgress(frame/30, story.cue_sec, story.motion_seconds);
  const before = storyState(story.previous_state ?? 'raise');
  const after = storyState(story.state);
  const blend = (key: keyof typeof before) => Number(before[key]) + (Number(after[key])-Number(before[key]))*p;
  const routeCost = blend('spending');
  const recurringRoute = blend('recurring') * (1-.64*blend('saving'));
  const layer = (role: string) => {
    const src = assetData[`wb-layer-${role}`];
    if (!src) throw new Error(`Missing WB illustration layer: ${role}`);
    return src;
  };
  const object = (role: string, x: number, y: number, label: string, opacity=1) => <div style={{position:'absolute',left:x,top:y,width:340,opacity}}>
    <Img src={layer(role)} style={{width:340,height:270,objectFit:'contain'}} />
    <div style={{padding:'10px 12px',color:'#F0E5D2',background:'rgba(20,38,51,.82)',borderRadius:10}}>
      <MeasuredText text={label} width={316} maxSize={34} minSize={30} maxLines={2} align="center" />
    </div>
  </div>;
  const path = (d: string, progress: number, opacity=1) => <path d={d} pathLength={1} fill="none" stroke="#D5AD6C" strokeWidth={5} strokeLinecap="round" strokeDasharray={1} strokeDashoffset={1-progress} opacity={opacity} />;
  return <SceneShell scene={scene} brand={brand} captionStyle="plain_phrase">
    <div style={{position:'absolute',inset:0,background:'#142633'}} />
    <Img src={layer('background')} style={{position:'absolute',inset:0,width:1080,height:1920,objectFit:'cover',opacity:.16}} />
    <div style={{position:'absolute',inset:0,background:'linear-gradient(180deg,rgba(12,26,36,.75),rgba(12,26,36,.28) 45%,rgba(12,26,36,.8))'}} />
    <div style={{position:'absolute',left:72,top:220,color:'#F0E5D2'}}>
      <MeasuredText text={scene.headline ?? ''} width={848} maxSize={56} minSize={48} maxLines={2} weight={700} />
    </div>
    <svg width={1080} height={1920} style={{position:'absolute',inset:0}} aria-label={story.state}>
      {path('M 420 560 C 515 560 525 730 565 730', blend('spending'), routeCost)}
      {path('M 410 414 C 660 414 780 460 780 610', blend('recurring'), recurringRoute)}
      {path('M 260 740 L 260 940', blend('saving'))}
      {path('M 420 1080 C 510 1080 520 1190 565 1190', blend('goal'))}
      <path d="M550 717 L568 730 L550 743" fill="none" stroke="#D5AD6C" strokeWidth={5} opacity={routeCost}/>
      <path d="M767 592 L780 610 L793 592" fill="none" stroke="#D5AD6C" strokeWidth={5} opacity={recurringRoute}/>
      <path d="M247 922 L260 940 L273 922" fill="none" stroke="#D5AD6C" strokeWidth={5} opacity={blend('saving')}/>
      <path d="M550 1177 L568 1190 L550 1203" fill="none" stroke="#D5AD6C" strokeWidth={5} opacity={blend('goal')}/>
    </svg>
    {object('income',110,430,'INCOME')}
    {object('costs',565,620,blend('recurring') >= .5 ? 'RECURRING COSTS' : 'SPENDING', .24+.76*blend('spending'))}
    {object('savings',110,940,'SAVINGS')}
    {object('goal',565,1030,'FUTURE GOAL',blend('goal'))}
    <div style={{position:'absolute',left:126,top:390,color:'#D5AD6C',fontSize:30,fontWeight:700,opacity:story.state === 'raise' ? p : 1}}>PAY RISE</div>
    <div style={{position:'absolute',left:330,top:842,color:'#D5AD6C',fontSize:30,fontWeight:700,opacity:blend('saving')}}>PLAN FIRST</div>
  </SceneShell>;
};
