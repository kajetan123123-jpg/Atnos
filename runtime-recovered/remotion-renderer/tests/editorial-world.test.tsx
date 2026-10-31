import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it, vi} from 'vitest';
vi.mock('remotion', async original => ({...await original<typeof import('remotion')>(),useCurrentFrame:()=>90}));
import {ReserveReadinessScene} from '../src/ReserveReadinessScene';
import {DEFAULT_BRAND, type ManifestScene} from '../src/contracts';
const scene=(label='CAR REPAIR'):ManifestScene=>({scene_id:'candidate',scene_type:'HookScene',
  timing:{start_sec:0,end_sec:5},narration:'Your car breaks down.',caption:'',assets:[],
  visual_relation:{kind:'reserve_readiness',stage:'need',objects:[{role:'expense',label}]}});
const brand={...DEFAULT_BRAND,presentation:'educational_illustration' as const,illustrationSystem:'wb_editorial_vector_v1' as const};
describe('opt-in editorial world preserves source meaning and existing defaults',()=>{
  it('shows one car and a functional character without duplicate invoice/card',()=>{
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene()} brand={brand}/>);
    expect(html).toContain('editorial-garage-v1'); expect(html).toContain('inspect-phone');
    expect(html.match(/data-wb-object="car-repair"/g)).toHaveLength(1);
    expect(html).not.toContain('essential-expense'); expect(html).not.toContain('curiosity-question');
  });
  it('leaves the existing presentation unchanged without explicit opt-in',()=>{
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene()} brand={{...DEFAULT_BRAND,presentation:'educational_illustration'}}/>);
    expect(html).not.toContain('alex-vector-candidate'); expect(html).toContain('curiosity-question');
  });
  it('does not substitute a car/host for a medical expense',()=>{
    const html=renderToStaticMarkup(<ReserveReadinessScene scene={scene('MEDICAL BILL')} brand={brand}/>);
    expect(html).not.toContain('editorial-garage-v1'); expect(html).not.toContain('car-repair');
    expect(html).toContain('essential-expense');
  });
});
