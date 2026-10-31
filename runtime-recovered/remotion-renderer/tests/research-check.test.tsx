import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {it,expect,vi} from 'vitest';
vi.mock('remotion',async original=>({...await original<typeof import('remotion')>(),useCurrentFrame:()=>120}));
import {ResearchCheckScene} from '../src/ResearchCheckScene';
import {DEFAULT_BRAND,type ManifestScene} from '../src/contracts';

it('requires all narrative-bound qualitative roles rather than creating data',()=>{
  const scene={scene_id:'s02',narration:'Check the company.',visual_relation:{kind:'research_check',objects:[]}} as unknown as ManifestScene;
  expect(()=>renderToStaticMarkup(<ResearchCheckScene scene={scene} brand={DEFAULT_BRAND}/>)).toThrow('Research diagram requires sourced subject, check and risk clauses');
});

it('shows qualitative check and risk sections without inventing a quantitative chart',()=>{
  const scene={scene_id:'s02',scene_type:'HookScene',timing:{start_sec:0,end_sec:8},headline:'Company',narration:'Check execution. Delays are a risk.',caption:'',assets:[],
    visual_relation:{kind:'research_check',objects:[{role:'subject',label:'Company'},{role:'check',label:'Check execution.'},{role:'risk',label:'Delays are a risk.'}]}} as ManifestScene;
  const html=renderToStaticMarkup(<ResearchCheckScene scene={scene} brand={DEFAULT_BRAND}/>);
  expect(html).toContain('WHAT TO CHECK');expect(html).toContain('WHAT CAN BREAK THE THESIS');
  expect(html).not.toContain('PERSONAL FINANCE');expect(html).not.toContain('<polyline');
});
