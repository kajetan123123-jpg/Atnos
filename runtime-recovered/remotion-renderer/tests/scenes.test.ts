import {describe, expect, it} from 'vitest';
import type {SemanticOverlayKind} from '../src/contracts';
import {
  chartValueLabel,
  hookCardCopyFor,
  hookMotionFor,
  hookNumbersFor,
  imageSceneTreatment,
  numberLayoutFor,
  numberMotionFor,
  numberVariantFor,
  semanticCompositionFor,
} from '../src/scenes';
import type {ManifestScene} from '../src/contracts';

const KINDS: SemanticOverlayKind[] = [
  'income_split',
  'payday_arrival',
  'convenience_chain',
  'cumulative_spending',
  'allocation_sorter',
  'auto_invest',
  'purchase_pause',
];

const COMPOUND_KINDS: SemanticOverlayKind[] = [
  'compound_layers',
  'compound_reinvestment',
  'compound_time',
  'compound_early_start',
  'compound_scenario',
];

describe('semantic image compositions', () => {
  it('uses photo-led, visibly varied layouts instead of one repeated card', () => {
    const layouts = KINDS.map(semanticCompositionFor);
    const treatments = new Set(layouts.map((layout) => layout.overlayTreatment));
    const positions = new Set(layouts.map((layout) => `${layout.overlayTop}:${layout.overlayLeft}:${layout.overlayScale}`));
    const motions = new Set(layouts.map((layout) => `${layout.panFrom}:${layout.panTo}`));

    expect(treatments).toEqual(new Set(['diagram', 'floating', 'none']));
    expect(positions.size).toBeGreaterThanOrEqual(5);
    expect(motions.size).toBe(KINDS.length);
    expect(layouts.filter((layout) => layout.overlayTreatment === 'none')).toHaveLength(2);
    expect(layouts.every((layout) => layout.overlayScale <= 0.67)).toBe(true);
  });

  it('rejects an unknown semantic layout instead of silently reusing a generic card', () => {
    expect(() => semanticCompositionFor('unknown' as SemanticOverlayKind)).toThrow(
      'Unsupported semantic composition',
    );
  });

  it('gives every compound-interest explanation an explicit deterministic layout', () => {
    const layouts = COMPOUND_KINDS.map(semanticCompositionFor);
    expect(layouts).toHaveLength(5);
    expect(layouts.every((layout) => layout.overlayTreatment === 'diagram')).toBe(true);
    expect(layouts.every((layout) => layout.overlayScale <= 0.67)).toBe(true);
  });
});

describe('number scene typography', () => {
  it('stacks a long ordinal comparison inside the safe width', () => {
    expect(numberLayoutFor('SECOND / FIRST')).toEqual({pieces: ['SECOND', 'FIRST'], fontSize: 200});
  });

  it('keeps a short source-backed number visually dominant', () => {
    expect(numberLayoutFor('20%')).toEqual({pieces: ['20%'], fontSize: 330});
  });

  it('handles typography stress tokens without empty layout', () => {
    const stress = ['OK', '10,000', '10%', '$10,000', '€10,000', 'Żółć', 'Wait—really?!'];
    for (const value of stress) {
      const layout = numberLayoutFor(value);
      expect(layout.pieces.length).toBeGreaterThan(0);
      expect(layout.fontSize).toBeGreaterThanOrEqual(100);
      expect(layout.fontSize).toBeLessThanOrEqual(330);
    }
  });

  it('maps each compound-interest beat to a distinct deterministic composition', () => {
    const scene = (headline: string): ManifestScene => ({
      scene_id: headline,
      scene_type: 'NumberScene',
      timing: {start_sec: 0, end_sec: 4},
      narration: headline,
      caption: headline,
      headline,
      number_value: '$100',
      assets: [],
    });

    expect([
      numberVariantFor(scene('YEAR TWO GAIN')),
      numberVariantFor(scene('YEAR ONE BALANCE')),
      numberVariantFor(scene('YEAR TWO BASE')),
      numberVariantFor({...scene('EXAMPLE, NOT A PROMISE'), caption: 'The second gain is only an example.'}),
    ]).toEqual(['gain', 'balance', 'rate', 'scenario']);
  });

  it('keeps deterministic motion active for the full scene instead of stopping after entry', () => {
    const start = numberMotionFor(0, 120);
    const middle = numberMotionFor(60, 120);
    const end = numberMotionFor(119, 120);

    expect(start.progress).toBe(0);
    expect(middle.progress).toBeGreaterThan(0.5);
    expect(end.progress).toBe(1);
    expect(start.drift).not.toBe(middle.drift);
  });
});

describe('source-backed chart labels', () => {
  it('formats the verified compound-interest progression without losing cents', () => {
    expect([100, 105, 110.25].map((value) => chartValueLabel(value, true))).toEqual([
      '$100',
      '$105',
      '$110.25',
    ]);
  });
});

describe('hook motion', () => {
  const scene = {
    scene_id: 'hook',
    scene_type: 'HookScene',
    timing: {start_sec: 0, end_sec: 3},
    narration: 'How $100 becomes $110.25 without another deposit.',
    caption: 'How $100 becomes $110.25 without another deposit.',
    assets: [],
  } satisfies ManifestScene;

  it('uses only source-backed values for the animated transformation', () => {
    expect(hookNumbersFor(scene)).toEqual(['$100', '$110.25']);
    expect(hookNumbersFor({...scene, narration: 'Time rewards patience.', caption: 'Time rewards patience.'})).toBeNull();
  });

  it('falls back to source caption instead of inventing financial labels', () => {
    const sourceOnly = {...scene, narration: 'Time rewards patience.', caption: 'Time rewards patience.'};

    expect(hookCardCopyFor(sourceOnly)).toEqual(['Time rewards patience.']);
    expect(hookCardCopyFor(sourceOnly)).not.toContain('START');
    expect(hookCardCopyFor(sourceOnly)).not.toContain('TIME + COMPOUNDING');
  });

  it('changes the visual state across the first three seconds', () => {
    const start = hookMotionFor(0, 90);
    const middle = hookMotionFor(45, 90);
    const end = hookMotionFor(89, 90);

    expect(start.progress).toBe(0);
    expect(middle.progress).toBeGreaterThan(0.45);
    expect(end.progress).toBe(1);
    expect(end.destinationOpacity).toBeGreaterThan(start.destinationOpacity);
    expect(end.destinationScale).toBeGreaterThan(start.destinationScale);
  });
});

describe('image scene grading', () => {
  it('keeps compound-interest photography readable under semantic overlays', () => {
    const treatment = imageSceneTreatment(true, true);

    expect(treatment.filter).toContain('brightness(1.18)');
    expect(treatment.filter).not.toContain('blur');
    expect(treatment.opacity).toBeGreaterThanOrEqual(0.9);
    expect(treatment.overlay).toContain('rgba(7,12,10,.04)');
  });

  it('retains the standard treatment for non-compound photography', () => {
    const treatment = imageSceneTreatment(false, false);

    expect(treatment.filter).toBe('brightness(1.30) contrast(1.01)');
    expect(treatment.opacity).toBe(1);
  });
});
