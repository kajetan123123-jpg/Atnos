import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {it,expect,vi} from 'vitest';
vi.mock('remotion',async original=>({...await original<typeof import('remotion')>(),useCurrentFrame:()=>60}));
import {ResearchIntroScene} from '../src/ResearchIntroScene';
import {DEFAULT_BRAND,type ManifestScene} from '../src/contracts';
const scene={scene_id:'s01',scene_type:'HookScene',timing:{start_sec:0,end_sec:4.4},
  narration:'Could these three speculative stocks grow ten times in value?',headline:'Could these three',caption:'',assets:[]} as ManifestScene;
it('preserves the entire actual spoken question instead of a truncated headline',()=>{
  const html=renderToStaticMarkup(<ResearchIntroScene scene={scene} brand={DEFAULT_BRAND} subjects={['Rocket Lab','SoFi','Hims and Hers']}/>);
  expect(html).toContain(scene.narration);
  for(const name of ['Rocket Lab','SoFi','Hims and Hers'])expect(html).toContain(`data-wb-research-alternative="${name}"`);
  expect(html).not.toContain('MONEY, EXPLAINED');
});
it('refuses to invent or duplicate a missing research alternative',()=>{
  expect(()=>renderToStaticMarkup(<ResearchIntroScene scene={scene} brand={DEFAULT_BRAND} subjects={['Rocket Lab','SoFi']}/>)).toThrow('three distinct');
});
