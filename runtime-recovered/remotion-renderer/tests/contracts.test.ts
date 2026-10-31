import {describe, expect, it} from 'vitest';
import {
  activeCaptionText,
  captionChunks,
  captionCuesFromWords,
  captionTextAtSceneTime,
  chartValues,
  durationInFrames,
  sceneFrameRange,
  DEFAULT_BRAND,
  headlineFor,
  numericToken,
  RENDERER_VERSION,
  SCENE_TYPES,
  selectImageAsset,
  type ProjectManifest,
  validateManifest,
} from '../src/contracts';

it('exposes exactly the five Wealth Blueprint V1 scene types', () => {
  expect(SCENE_TYPES).toEqual(['HookScene', 'ImageScene', 'NumberScene', 'ChartScene', 'CTAScene']);
  expect(() =>
    validateManifest(manifest({...baseScene, scene_type: 'ComparisonScene' as never})),
  ).toThrow('unsupported scene_type');
});

it('keeps the versioned Wealth Blueprint palette above the near-black baseline', () => {
  expect(DEFAULT_BRAND.background).toBe('#142633');
  expect(DEFAULT_BRAND.panel).toBe('#1C3344');
});

const manifest = (scene: ProjectManifest['scenes'][number]): ProjectManifest => ({
  schema_version: '1.0',
  manifest_id: 'manifest-job-1',
  job_id: 'job-1',
  project_id: 'wealth-blueprint',
  channel: 'wealth_blueprint',
  language_code: 'en',
  brand_profile_version: 'brand-v1-test',
  renderer_version: RENDERER_VERSION,
  content_hash: 'a'.repeat(64),
  scenes: [scene],
});

const baseScene: ProjectManifest['scenes'][number] = {
  scene_id: 's01',
  scene_type: 'HookScene',
  timing: {start_sec: 0, end_sec: 2.25},
  narration: 'Start with a plan.',
  caption: 'Start with a plan.',
  headline: 'Start with a plan',
  assets: [],
};

describe('manifest contract', () => {
  it('requires a native reserve board to carry its actual qualitative roles and stage', () => {
    const reserve = {...baseScene, semantic_overlay_kind:'reserve_readiness' as const,
      visual_relation:{kind:'reserve_readiness',stage:'separate',objects:[
        {role:'reserve',label:'READY RESERVE'},{role:'goal',label:'FUTURE GOAL'}]}};
    expect(validateManifest(manifest(reserve)).scenes[0].visual_relation?.stage).toBe('separate');
    expect(() => validateManifest(manifest({...reserve,visual_relation:{kind:'reserve_readiness',stage:'unknown'}}))).toThrow('meaning contract');
    expect(() => validateManifest(manifest({...reserve,scene_type:'ImageScene'}))).toThrow('meaning contract');
  });
  it('accepts a deterministic supported scene and calculates exact frames', () => {
    const parsed = validateManifest(manifest(baseScene));
    expect(durationInFrames(parsed)).toBe(68);
    expect(headlineFor(baseScene)).toBe('START WITH A PLAN');
  });

  it('covers each frame exactly once at the recorded full-trial speech boundaries', () => {
    const boundaries = [0, 7.544, 14.348, 21.231, 27.815, 34.018, 37.2];
    const scenes = boundaries.slice(0, -1).map((start, index) => ({...baseScene,
      scene_id: `s${index + 1}`, timing: {start_sec: start, end_sec: boundaries[index + 1]}}));
    const actual = {...manifest(baseScene), scenes};
    const coverage = Array(durationInFrames(actual)).fill(0);
    scenes.forEach((scene, index) => {
      const range = sceneFrameRange(scene, index === scenes.length - 1);
      for (let frame = range.from; frame < range.from + range.durationInFrames; frame++) coverage[frame]++;
    });
    expect(coverage).toHaveLength(1116);
    expect(coverage.every((count) => count === 1)).toBe(true);
    expect(sceneFrameRange(scenes[2])).toEqual({from: 430, durationInFrames: 207});
    expect(coverage[636]).toBe(1);
    expect(coverage[834]).toBe(1);
    expect(coverage[1020]).toBe(1);
  });

  it('holds the last scene through the composition ceil frame without extending internal cuts', () => {
    const first = {...baseScene, timing: {start_sec: 0, end_sec: 2.251}};
    const last = {...baseScene, timing: {start_sec: 2.251, end_sec: 4.001}};
    const a = sceneFrameRange(first);
    const b = sceneFrameRange(last, true);
    expect(a.from + a.durationInFrames).toBe(b.from);
    expect(b.from + b.durationInFrames).toBe(durationInFrames({...manifest(first), scenes: [first, last]}));
    expect(b.from + b.durationInFrames).toBe(121);
  });

  it('rejects unsupported scene types', () => {
    expect(() => validateManifest(manifest({...baseScene, scene_type: 'VideoScene' as never}))).toThrow(
      'unsupported scene_type',
    );
  });

  it('rejects a NumberScene without a source-backed number', () => {
    expect(() => validateManifest(manifest({...baseScene, scene_type: 'NumberScene'}))).toThrow(
      'has no source-backed number',
    );
  });

  it('rejects a ChartScene without at least two source-backed values', () => {
    expect(() =>
      validateManifest(manifest({...baseScene, scene_type: 'ChartScene', caption: 'Growth reached 10%'})),
    ).toThrow('needs at least two source-backed values');
  });

  it('extracts only values present in source text', () => {
    const numberScene = {...baseScene, scene_type: 'NumberScene' as const, caption: 'Save 20% every month.'};
    const chartScene = {...baseScene, scene_type: 'ChartScene' as const, caption: 'It moved from 10 to 18 to 27.'};
    expect(numericToken(numberScene)).toBe('20%');
    expect(chartValues(chartScene)).toEqual([10, 18, 27]);
  });

  it('accepts chart labels only when they match every source-backed value', () => {
    const scene = {
      ...baseScene,
      scene_type: 'ChartScene' as const,
      caption: 'Starting at $100, year 1 reaches $105 and year 2 reaches $110.25.',
      chart_values: [100, 105, 110.25],
      chart_labels: ['START', 'YEAR 1', 'YEAR 2'],
    };
    expect(validateManifest(manifest(scene))).toBeTruthy();
    expect(() => validateManifest(manifest({...scene, chart_labels: ['START', 'YEAR 1']}))).toThrow(
      'labels must match',
    );
    expect(() => validateManifest(manifest({...scene, chart_labels: ['START', 'FABRICATED', 'YEAR 2']}))).toThrow(
      'labels must match',
    );
    expect(() => validateManifest(manifest({...scene, chart_labels: ['YEAR 2', 'YEAR 1', 'START']}))).toThrow(
      'labels must match',
    );
  });

  it('accepts an explicit empty label list when narration supports values but not labels', () => {
    const scene = {
      ...baseScene,
      scene_type: 'ChartScene' as const,
      caption: 'Watch the balance move from $100 to $105 to $110.25 as each period compounds the larger base.',
      chart_values: [100, 105, 110.25],
      chart_labels: [],
    };

    expect(validateManifest(manifest(scene))).toBeTruthy();
  });

  it('accepts a compound progression sourced across the canonical scene sequence', () => {
    const compoundManifest = manifest({
      ...baseScene,
      scene_id: 's03',
      scene_type: 'ChartScene',
      timing: {start_sec: 6, end_sec: 10},
        narration: 'In year two, the $5.25 gain moves the balance to $110.25.',
        caption: 'In year two, the $5.25 gain moves the balance to $110.25.',
      semantic_overlay_kind: 'compound_reinvestment',
      chart_values: [100, 105, 110.25],
      chart_labels: ['START', 'YEAR 1', 'YEAR 2'],
    });
    compoundManifest.scenes = [
      {
        ...baseScene,
        scene_id: 's01',
        timing: {start_sec: 0, end_sec: 3},
        narration: 'Start with $100.',
        caption: 'Start with $100.',
      },
      {
        ...baseScene,
        scene_id: 's02',
        timing: {start_sec: 3, end_sec: 6},
        narration: 'After year one, the balance is $105.',
        caption: 'After year one, the balance is $105.',
      },
      compoundManifest.scenes[0],
    ];

    expect(validateManifest(compoundManifest)).toBeTruthy();
  });

  it('does not let an unrelated chart borrow values from other scenes', () => {
    const unrelatedManifest = manifest({
      ...baseScene,
      scene_id: 's03',
      scene_type: 'ChartScene',
      timing: {start_sec: 6, end_sec: 10},
      narration: 'This comparison shows $5.25 beside $110.25.',
      caption: 'This comparison shows $5.25 beside $110.25.',
      semantic_overlay_kind: 'income_split',
      chart_values: [100, 105, 110.25],
    });
    unrelatedManifest.scenes = [
      {...baseScene, scene_id: 's01', timing: {start_sec: 0, end_sec: 3}, caption: 'Start with $100.'},
      {...baseScene, scene_id: 's02', timing: {start_sec: 3, end_sec: 6}, caption: 'Then $105.'},
      unrelatedManifest.scenes[0],
    ];

    expect(() => validateManifest(unrelatedManifest)).toThrow('not backed by narration');
  });

  it('rejects a fabricated value even for a compound progression', () => {
    const compoundManifest = manifest({
      ...baseScene,
      scene_id: 's03',
      scene_type: 'ChartScene',
      timing: {start_sec: 6, end_sec: 10},
      narration: 'In year 2, the balance reaches $110.25.',
      caption: 'In year 2, the balance reaches $110.25.',
      semantic_overlay_kind: 'compound_reinvestment',
      chart_values: [100, 999, 110.25],
    });
    compoundManifest.scenes = [
      {...baseScene, scene_id: 's01', timing: {start_sec: 0, end_sec: 3}, caption: 'Start with $100.'},
      {...baseScene, scene_id: 's02', timing: {start_sec: 3, end_sec: 6}, caption: 'After year 1: $105.'},
      compoundManifest.scenes[0],
    ];

    expect(() => validateManifest(compoundManifest)).toThrow('not backed by narration');
  });

  it('rejects repeated explicit chart values without repeated source evidence', () => {
    expect(() =>
      validateManifest(
        manifest({
          ...baseScene,
          scene_type: 'ChartScene',
          caption: 'From $100 to $105.',
          chart_values: [100, 105, 100, 105],
        }),
      ),
    ).toThrow('reorders, repeats, or invents values');
  });

  it('rejects chart values that reverse source order', () => {
    expect(() =>
      validateManifest(
        manifest({
          ...baseScene,
          scene_type: 'ChartScene',
          caption: 'From $100 to $105 and then $110.25.',
          chart_values: [110.25, 105, 100],
        }),
      ),
    ).toThrow('reorders, repeats, or invents values');
  });

  it.each([
    {input: [100, 'not-a-number', 105]},
    {input: [100, Number.NaN, 105]},
    {input: [100, Number.POSITIVE_INFINITY, 105]},
  ])('rejects malformed explicit chart values: $input', ({input}) => {
    expect(() =>
      validateManifest(
        manifest({
          ...baseScene,
          scene_type: 'ChartScene',
          caption: 'From $100 to $105.',
          chart_values: input as never,
        }),
      ),
    ).toThrow('chart_values must be finite numbers');
  });

  it('requires source-backed values and two explicit labels for a deterministic comparison', () => {
    expect(() =>
      validateManifest(
        manifest({...baseScene, scene_type: 'ChartScene', comparison_left: 'CONSISTENT CONTRIBUTIONS'}),
      ),
    ).toThrow(
      'comparison needs two explicit labels',
    );
    expect(
      validateManifest(
        manifest({
          ...baseScene,
          scene_type: 'ChartScene',
          caption: 'Consistent contributions: 12 periods. Financial hype: 1 promise.',
          chart_values: [12, 1],
          comparison_left: 'CONSISTENT CONTRIBUTIONS',
          comparison_right: 'FINANCIAL HYPE',
        }),
      ),
    ).toBeTruthy();
    expect(() =>
      validateManifest(
        manifest({
          ...baseScene,
          scene_type: 'ChartScene',
          comparison_left: 'CONSISTENT CONTRIBUTIONS',
          comparison_right: 'FINANCIAL HYPE',
        }),
      ),
    ).toThrow('source-backed values');
  });

  it('chunks captions into at most three words', () => {
    expect(captionChunks('one two three four five six seven')).toEqual(['one two three', 'four five six', 'seven']);
  });

  it('keeps subtitle text independent from a different headline', () => {
    const scene = {
      ...baseScene,
      headline: 'Raise summary',
      overlay_text: 'Raise summary',
      narration: 'Your paycheck feels bigger this year.',
      caption: 'Your paycheck feels bigger this year.',
      caption_words: [
        {text: 'Your', start_sec: 0.0, end_sec: 0.2},
        {text: 'paycheck', start_sec: 0.2, end_sec: 0.5},
        {text: 'feels', start_sec: 0.5, end_sec: 0.7},
        {text: 'bigger', start_sec: 0.7, end_sec: 1.0},
        {text: 'this', start_sec: 1.0, end_sec: 1.2},
        {text: 'year.', start_sec: 1.2, end_sec: 1.6},
      ],
    };
    expect(validateManifest(manifest(scene))).toBeTruthy();
    expect(headlineFor(scene, 7)).toContain('RAISE');
    expect(captionTextAtSceneTime(scene, 0)).toBe('Your paycheck feels bigger');
    expect(captionTextAtSceneTime(scene, Math.round(0.75 * 30))).toContain('bigger');
    expect(captionTextAtSceneTime(scene, Math.round(1.3 * 30))).toContain('year');
    expect(captionTextAtSceneTime(scene, 0)).not.toContain('Raise');
  });

  it('keeps first and last word cues visible at scene edges', () => {
    const words = [
      {text: 'Save', start_sec: 0.05, end_sec: 0.35},
      {text: 'first,', start_sec: 0.35, end_sec: 0.7},
      {text: 'then', start_sec: 0.7, end_sec: 0.95},
      {text: 'spend', start_sec: 0.95, end_sec: 1.3},
      {text: 'without', start_sec: 1.3, end_sec: 1.7},
      {text: 'guilt.', start_sec: 1.7, end_sec: 2.2},
    ];
    const cues = captionCuesFromWords(words);
    expect(activeCaptionText(cues, 0.1)).toContain('Save');
    expect(activeCaptionText(cues, 2.0)).toContain('guilt.');
    // Inclusive hold through last word end_sec; clear only after the cue ends.
    expect(activeCaptionText(cues, 2.2)).toContain('guilt.');
    expect(activeCaptionText(cues, 2.21)).toBe('');
  });

  it('does not end a caption plate on a preposition that the next word completes', () => {
    const phrase = 'so money leaves checking on Friday and lands in savings'.split(' ');
    const words = phrase.map((text, index) => ({
      text: index === phrase.length - 1 ? `${text}.` : text,
      start_sec: index * 0.3,
      end_sec: index * 0.3 + 0.25,
    }));
    const cues = captionCuesFromWords(words);
    for (const cue of cues) {
      const last = cue.text.replace(/[^A-Za-z]+$/g, '').split(' ').pop()?.toLowerCase();
      expect(['on', 'to', 'from', 'with', 'and', 'or', 'because']).not.toContain(last);
    }
    expect(cues.map((cue) => cue.text).join(' | ')).toMatch(/on Friday/i);
  });

  it('does not end a plate on how or over when the next word finishes the phrase', () => {
    const phrase = 'decide how much to keep and adds up over time'.split(' ');
    const words = phrase.map((text, index) => ({
      text: index === phrase.length - 1 ? `${text}.` : text,
      start_sec: index * 0.28,
      end_sec: index * 0.28 + 0.24,
    }));
    const cues = captionCuesFromWords(words);
    for (const cue of cues) {
      const last = cue.text.replace(/[^A-Za-z]+$/g, '').split(' ').pop()?.toLowerCase();
      expect(['how', 'over', 'and', 'to']).not.toContain(last);
    }
    expect(cues.map((cue) => cue.text).join(' | ')).toMatch(/how much/i);
    expect(cues.map((cue) => cue.text).join(' | ')).toMatch(/over time/i);
  });

  it('rejects caption text that does not equal caption_words', () => {
    expect(() =>
      validateManifest(
        manifest({
          ...baseScene,
          caption: 'Wrong subtitle',
          caption_words: [
            {text: 'Start', start_sec: 0, end_sec: 0.4},
            {text: 'with', start_sec: 0.4, end_sec: 0.8},
            {text: 'a', start_sec: 0.8, end_sec: 1.0},
            {text: 'plan.', start_sec: 1.0, end_sec: 1.5},
          ],
        }),
      ),
    ).toThrow('caption must equal joined caption_words');
  });

  it('requires one QA-passed selected image and never falls back to a candidate', () => {
    const candidateOnly = {
      ...baseScene,
      scene_type: 'ImageScene' as const,
      assets: [
        {
          asset_id: 'candidate-1',
          role: 'image_candidate' as const,
          path: 'candidate.png',
          exists: true,
          provenance: {artifact_hash: 'b'.repeat(64)},
          qa: {status: 'passed' as const, action: 'PASS' as const},
        },
      ],
    };
    expect(() => validateManifest(manifest(candidateOnly))).toThrow('exactly one QA-passed selected image');
    expect(selectImageAsset(candidateOnly, {'candidate-1': 'data:image/png;base64,abc'})).toBeNull();

    const selected = {
      ...candidateOnly,
      assets: [
        ...candidateOnly.assets,
        {
          asset_id: 'selected-1',
          role: 'selected_image' as const,
          path: 'selected.png',
          exists: true,
          provenance: {artifact_hash: 'c'.repeat(64)},
          qa: {status: 'passed' as const, action: 'PASS' as const},
        },
      ],
    };
    expect(validateManifest(manifest(selected))).toBeTruthy();
    expect(selectImageAsset(selected, {'candidate-1': 'candidate', 'selected-1': 'selected'})).toBe('selected');

    const semantic = {...selected, semantic_overlay_kind: 'income_split' as const};
    expect(validateManifest(manifest(semantic))).toBeTruthy();
    const compoundSemantic = {...selected, semantic_overlay_kind: 'compound_reinvestment' as const};
    expect(validateManifest(manifest(compoundSemantic))).toBeTruthy();
    expect(() =>
      validateManifest(manifest({...selected, semantic_overlay_kind: 'unknown_overlay' as never})),
    ).toThrow('unsupported semantic_overlay_kind');
  });
});
