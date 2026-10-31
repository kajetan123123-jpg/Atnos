import {describe,it,expect} from 'vitest';
import type {ManifestScene} from '../src/contracts';
import {editorialDirection,editorialState} from '../src/editorial-motion';
const fixture=()=>({scene_id:'s02',scene_type:'ImageScene',timing:{start_sec:4,end_sec:12},narration:'Inspect execution. Assess financing risk.',
 caption:'',assets:[],caption_words:[{text:'Inspect',start_sec:4,end_sec:4.5}],
 visual_relation:{kind:'research_check',objects:[{role:'subject',label:'Subject'},{role:'check',label:'Inspect execution.'},{role:'risk',label:'Assess financing risk.'}],
 editorial_direction:{version:1,mode:'execution_to_cash',narration_hash:'fixture',timing_source:'final_voice_word_alignment',duration_sec:8,measurement:'schematic_not_company_data',hold_seconds:.8,action_sec:.35,risk_sec:4,subject:'Subject',check:'Inspect execution.',risk:'Assess financing risk.',visible_change:'Break the connector at the financing cue.'}}} as ManifestScene);
describe('audio-bound editorial direction',()=>{
 it('keeps risk absent before its cue and holds it after its cue',()=>{
  const s=fixture();expect(editorialState(s,3.9).risk).toBe(0);expect(editorialState(s,4.6).risk).toBe(1);
  expect(editorialState(s,7.8).risk).toBe(1);expect(editorialState(s,.9).build).toBeGreaterThan(0);
 });
 it('does not turn unsupported meaning into a generic card',()=>{
  const s=fixture();s.visual_relation!.editorial_direction!.mode='generic';expect(()=>editorialDirection(s)).toThrow('Unsupported');
 });
 it('rejects stale clauses and impossible timing',()=>{
  const s=fixture();s.visual_relation!.editorial_direction!.check='New claim';expect(()=>editorialDirection(s)).toThrow();
  const t=fixture();t.visual_relation!.editorial_direction!.risk_sec=7.9;expect(()=>editorialDirection(t)).toThrow();
 });
});
