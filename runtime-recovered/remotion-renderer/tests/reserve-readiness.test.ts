import {describe, expect, it} from 'vitest';
import {reserveStageFor, reserveCueFrame, reserveExpenseFor, RESERVE_STAGES} from '../src/ReserveReadinessScene';
import type {ManifestScene} from '../src/contracts';

describe('qualitative reserve board contract', () => {
  it.each(RESERVE_STAGES)('renders the actual %s meaning stage without defaulting to another story', (stage) => {
    const scene = {scene_id:'isolated', visual_relation:{kind:'reserve_readiness',stage}} as ManifestScene;
    expect(reserveStageFor(scene)).toBe(stage);
  });
  it.each([undefined,'unknown','raise_split','guaranteed_growth'])('rejects unsupported stage %s', (stage) => {
    const scene = {scene_id:'isolated', visual_relation:{kind:'reserve_readiness',stage}} as ManifestScene;
    expect(() => reserveStageFor(scene)).toThrow('explicit meaning stage');
  });
  it('keeps both alternatives visible before a late final-word cue', () => {
    const scene = {timing:{start_sec:20, end_sec:28}, caption_words:[{text:'time.',start_sec:27.7,end_sec:28,segment_id:'s04'}]} as ManifestScene;
    expect(reserveCueFrame(scene, ['time'], 52)).toBeLessThanOrEqual(75);
  });
  it('preserves an early final-audio cue', () => {
    const scene = {timing:{start_sec:20,end_sec:28},caption_words:[{text:'rather',start_sec:20.8,end_sec:21,segment_id:'s04'}]} as ManifestScene;
    expect(reserveCueFrame(scene,['rather'],52)).toBe(24);
  });
  it('lets a later access cue follow its phrase while retaining a three-second hold',()=>{
    const scene={timing:{start_sec:10,end_sec:18},caption_words:[
      {text:'access',start_sec:14,end_sec:14.2,segment_id:'s03'},
      {text:'conditions',start_sec:17,end_sec:17.2,segment_id:'s03'}]} as ManifestScene;
    expect(reserveCueFrame(scene,['access'],52,240)).toBe(120);
    expect(reserveCueFrame(scene,['conditions'],52,240)).toBe(150);
  });
  it('selects the actual expense rather than forcing a car into another story', () => {
    const scene=(label:string) => ({visual_relation:{objects:[{role:'expense',label}]}} as ManifestScene);
    expect(reserveExpenseFor(scene('CAR REPAIR'))).toBe('car');
    expect(reserveExpenseFor(scene('MEDICAL BILL'))).toBe('medical');
    expect(reserveExpenseFor(scene('UNEXPECTED NEED'))).toBe('unspecified');
  });
});
