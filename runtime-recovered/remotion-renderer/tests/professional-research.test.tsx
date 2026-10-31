import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {it,expect,vi} from 'vitest';
vi.mock('remotion',async original=>({...await original<typeof import('remotion')>(),useCurrentFrame:()=>60}));
import {ProfessionalResearchScene,speakingAmount} from '../src/ProfessionalResearchScene';
import {DEFAULT_BRAND,type ManifestScene} from '../src/contracts';
const scene={scene_id:'s01',scene_type:'HookScene',timing:{start_sec:0,end_sec:4.4},
 narration:'Could these three speculative stocks grow ten times in value?',headline:'Could these three',caption:'',assets:[],
 caption_words:[{text:'Could',start_sec:0,end_sec:.5}]} as ManifestScene;
it('preserves full question and actual alternatives with square panels',()=>{
 const html=renderToStaticMarkup(<ProfessionalResearchScene scene={scene} brand={{...DEFAULT_BRAND,visualVariant:'professional'}} subjects={['Rocket Lab','SoFi','Hims and Hers']} assetData={{}}/>);
 expect(html).toContain(scene.narration);
 for(const name of ['Rocket Lab','SoFi','Hims and Hers'])expect(html).toContain(name);
 expect(html).not.toContain('border-radius:12');
 expect(html).toContain('NOT A FORECAST');
});
it('never substitutes a placeholder for missing registered Alex',()=>{
 expect(()=>renderToStaticMarkup(<ProfessionalResearchScene scene={scene} brand={{...DEFAULT_BRAND,visualVariant:'professional_host'}} subjects={[]} assetData={{}}/>)).toThrow('registered Alex');
});
it('mouth motion uses actual word time and stops in pauses',()=>{
 expect(speakingAmount(scene,-.1)).toBe(0);
 expect(speakingAmount(scene,.25)).toBeGreaterThan(.5);
 expect(speakingAmount(scene,.8)).toBe(0);
});
it('keeps the host below the complete three-line opening question',()=>{
 const html=renderToStaticMarkup(<ProfessionalResearchScene scene={scene} brand={{...DEFAULT_BRAND,visualVariant:'professional_host'}} subjects={['Rocket Lab','SoFi','Hims and Hers']} assetData={{wb_registered_presenter:'data:image/png;base64,fixture'}}/>);
 expect(html).toContain('top:470px');
 expect(html).toContain('top:490px');
});
