import type {ManifestScene} from './contracts';

export type CalculationClaim = {
  id: string; operation: string; operands: number[]; result: number; unit: string;
};
export type VerifiedCompoundSeries = {
  version: 1; claim_id: string; calculation_hash: string;
  convention: 'annual_compounding_no_additional_deposits';
  principal: number; annual_rate: number; horizon_years: number; unit: 'dollars' | 'PLN';
  years: number[]; values: number[]; display_values: number[];
  assumption_label: string; exclusions_label: string;
};
const near = (a: number, b: number) => Number.isFinite(a) && Number.isFinite(b) && Math.abs(a-b) <= 1e-8;

export function verifyCompoundSeries(scene: ManifestScene, claims: CalculationClaim[]): void {
  const s = scene.verified_series;
  if (!s) return;
  const matches = claims.filter(claim => claim.id === s.claim_id);
  if (matches.length !== 1) throw new Error('Derived compound series references an unknown or duplicate claim');
  const c = matches[0];
  const [principal, rate, years] = c.operands;
  if (s.version !== 1 || s.convention !== 'annual_compounding_no_additional_deposits'
      || !/^[a-f0-9]{64}$/.test(s.calculation_hash) || c.operation !== 'compound' || c.operands.length !== 3
      || !['dollars','PLN'].includes(c.unit) || principal <= 0 || !Number.isFinite(principal)
      || rate <= 0 || rate > 1 || !Number.isInteger(years) || years < 3 || years > 100
      || !near(c.result, principal * (1+rate)**years)
      || s.unit !== c.unit || !near(s.principal,principal) || !near(s.annual_rate,rate) || s.horizon_years !== years) {
    throw new Error('Derived compound series has invalid calculation assumptions');
  }
  const periods = [0, Math.floor(years/3), Math.floor(2*years/3), years];
  const expected = periods.map(year => principal * (1+rate)**year);
  if (s.years?.length !== 4 || s.values?.length !== 4 || s.display_values?.length !== 4
      || periods.some((year,i) => s.years[i] !== year || !near(s.values[i],expected[i])
        || s.display_values[i] !== Math.floor(expected[i]+.5))
      || scene.scene_type !== 'ChartScene' || scene.claim_ids?.length !== 1 || scene.claim_ids[0] !== c.id
      || scene.chart_unit !== c.unit || scene.chart_values?.length !== 4
      || expected.some((value,i) => !near(scene.chart_values![i],value))
      || scene.chart_labels?.length !== 4 || periods.some((year,i) => scene.chart_labels![i] !== `YEAR ${year}`)
      || !s.assumption_label.startsWith('Hypothetical · ') || !s.assumption_label.endsWith('% annually')
      || !near(Number(s.assumption_label.slice(15).split('%')[0]),rate*100)
      || s.exclusions_label !== 'Before fees, taxes and inflation') {
    throw new Error('Derived compound series differs from its verified calculation');
  }
}

export function compoundTiming(scene: ManifestScene) {
  const duration = scene.timing.end_sec-scene.timing.start_sec;
  const words = scene.caption_words ?? [];
  const clean = (word: string) => word.toLowerCase().replace(/[^a-z]/g,'');
  const startWord = words.find(word => /^(earned|earning|assumed|if)$/.test(clean(word.text)));
  const endWord = [...words].reverse().find(word => /^(hundred|result|balance|dollars)$/.test(clean(word.text)));
  const start = Math.max(0, Math.min(duration-1.2, (startWord?.start_sec ?? scene.timing.start_sec+.25)-scene.timing.start_sec));
  const end = Math.min(duration-.4, Math.max(start+1, (endWord?.end_sec ?? scene.timing.end_sec-1.4)-scene.timing.start_sec));
  return {start, end, source: words.length ? 'final_voice_word_alignment' : 'scene_timing'};
}

export function compoundProgress(scene: ManifestScene, seconds: number) {
  const {start,end} = compoundTiming(scene);
  const t = Math.max(0,Math.min(1,(seconds-start)/Math.max(.1,end-start)));
  return t*t*(3-2*t);
}

export const COMPOUND_FRAME = {left:72, width:848, headingTop:210, plotTop:540, plotHeight:640,
  noteTop:1240, captionClearance:1420} as const;
