import {describe,expect,it} from 'vitest';
import {validateManifest,RENDERER_VERSION,type ProjectManifest,type ManifestScene} from '../src/contracts';
import {COMPOUND_FRAME,compoundTiming,compoundProgress,type VerifiedCompoundSeries} from '../src/compound-series';
import {fitText} from '../src/typography';

const values=[0,10,20,30].map(year=>10*1.08**year);
const series:VerifiedCompoundSeries={version:1,claim_id:'c1',calculation_hash:'a'.repeat(64),
  convention:'annual_compounding_no_additional_deposits',principal:10,annual_rate:.08,horizon_years:30,
  unit:'dollars',years:[0,10,20,30],values,display_values:[10,22,47,101],
  assumption_label:'Hypothetical · 8% annually',exclusions_label:'Before fees, taxes and inflation'};
const scene:ManifestScene={scene_id:'s01',scene_type:'ChartScene',timing:{start_sec:0,end_sec:9},
  narration:'If ten dollars earned eight percent a year, it could grow to roughly one hundred dollars.',
  caption:'If ten dollars earned eight percent a year, it could grow to roughly one hundred dollars.',
  headline:'The other path',chart_values:values,chart_labels:['YEAR 0','YEAR 10','YEAR 20','YEAR 30'],
  claim_ids:['c1'],chart_unit:'dollars',verified_series:series,assets:[]};
const manifest=(s:ManifestScene=scene):ProjectManifest=>({schema_version:'1.0',manifest_id:'m',job_id:'j',
  project_id:'wealth-blueprint',channel:'wealth_blueprint',language_code:'en',brand_profile_version:'b',
  renderer_version:RENDERER_VERSION,content_hash:'a'.repeat(64),scenes:[s],
  calculation_claims:[{id:'c1',operation:'compound',operands:[10,.08,30],result:values[3],unit:'dollars'}]});

describe('normal derived compound chart',()=>{
  it('accepts checkpoints derived from one claim without inventing spoken intermediate values',()=>{
    expect(validateManifest(manifest()).scenes[0].chart_values).toEqual(values);
  });
  it.each(['values','display_values','years','annual_rate','assumption_label'] as const)('rejects changed %s',field=>{
    const bad=structuredClone(scene); const s=bad.verified_series!;
    if(field==='annual_rate')s.annual_rate=.1;
    else if(field==='assumption_label')s.assumption_label='Guaranteed · 8% annually';
    else s[field][1]=99;
    expect(()=>validateManifest(manifest(bad))).toThrow('Derived compound series');
  });
  it('rejects unknown claim and incorrect calculated result',()=>{
    const missing=manifest();missing.calculation_claims=[];
    expect(()=>validateManifest(missing)).toThrow('unknown');
    const wrong=manifest();wrong.calculation_claims![0].result=200;
    expect(()=>validateManifest(wrong)).toThrow('invalid calculation');
  });
  it('matches motion to voice cues and leaves a final reading hold',()=>{
    const timed={...scene,caption_words:[{text:'If',start_sec:1,end_sec:1.2},{text:'hundred',start_sec:6,end_sec:6.4}]};
    expect(compoundTiming(timed)).toEqual({start:1,end:6.4,source:'final_voice_word_alignment'});
    expect(compoundProgress(timed,.9)).toBe(0);
    expect(compoundProgress(timed,3)).toBeGreaterThan(0);
    expect(compoundProgress(timed,6.4)).toBe(1);
    expect(compoundProgress(timed,8.5)).toBe(1);
    expect(compoundTiming(scene).source).toBe('scene_timing');
  });
  it('keeps honest assumptions and chart bounds clear of captions at 1080 and 360 widths',()=>{
    expect(COMPOUND_FRAME.left+COMPOUND_FRAME.width).toBeLessThanOrEqual(1008);
    expect(COMPOUND_FRAME.plotTop+COMPOUND_FRAME.plotHeight).toBeLessThan(COMPOUND_FRAME.noteTop);
    expect(COMPOUND_FRAME.noteTop+140).toBeLessThan(COMPOUND_FRAME.captionClearance);
    for(const width of [848,848/3]){
      const ratio=width/848;
      const fitted=fitText(series.assumption_label,width,38*ratio,32*ratio,2,(text,size)=>text.length*size*.64);
      expect(fitted.lines.join(' ')).toBe(series.assumption_label);
    }
  });
});
