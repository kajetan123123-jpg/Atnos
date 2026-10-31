import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it,vi} from 'vitest';
vi.mock('remotion',async original=>({...await original<typeof import('remotion')>(),useCurrentFrame:()=>180}));
import {StyleComparisonScene,comparisonSupported,comparisonCue} from '../src/StyleComparisonScene';
import {DEFAULT_BRAND,type ManifestScene} from '../src/contracts';
const scene={scene_id:'s1',scene_type:'HookScene',timing:{start_sec:3,end_sec:9},
  narration:'Name the emergency reserve and future purchase.',headline:'Same money. Two jobs.',caption:'',assets:[],
  visual_relation:{kind:'reserve_readiness',stage:'separate',objects:[]},
  caption_words:[{text:'reserve',start_sec:4,end_sec:4.5}]} as ManifestScene;
it('preserves the qualitative meaning and refuses unrelated research scenes',()=>{
  expect(comparisonSupported(scene)).toBe(true);
  expect(comparisonSupported({...scene,visual_relation:{kind:'research_check'}})).toBe(false);
  expect(()=>renderToStaticMarkup(<StyleComparisonScene scene={{...scene,visual_relation:{kind:'research_check'}}} brand={{...DEFAULT_BRAND,comparisonStyle:'editorial_magazine'}}/>)).toThrow('qualitative');
});
it('uses the final caption cue relative to the actual scene',()=>{
  expect(comparisonCue(scene,['reserve'],100)).toBe(30);
  expect(comparisonCue(scene,['absent'],100)).toBe(100);
});
it.each(['illustrated_explainer','pigment_motion','editorial_magazine'] as const)('has a distinct explicit candidate layout %s without fabricated amounts',style=>{
  const html=renderToStaticMarkup(<StyleComparisonScene scene={scene} brand={{...DEFAULT_BRAND,comparisonStyle:style}}/>);
  expect(html).toContain('data-wb-comparison-style="'+style+'"');
  expect(html).toContain('not a numerical allocation');
  expect(html).not.toContain('$');
  expect(html).not.toContain('data-wb-active-word');
});
it('painted direction uses the existing layered WB world',()=>{
  const html=renderToStaticMarkup(<StyleComparisonScene scene={scene} brand={{...DEFAULT_BRAND,comparisonStyle:'painted_editorial'}}/>);
  expect(html).toContain('planning-desk-candidate-v1');
  expect(html).toContain('fixed-gouache-pigments');
});
