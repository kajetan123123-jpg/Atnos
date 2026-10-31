import {describe, expect, it} from 'vitest';
import {fitText} from '../src/typography';
import {activeCaptionText, captionCuesFromWords, CAPTION_MAX_WORDS, CAPTION_MIN_FONT_PX, DEFAULT_CAPTION_STYLE, imageHeadlineFor} from '../src/contracts';
import {meaningDiagramReplacesUnderlay, usesBackgroundIllustrationFrame} from '../src/scenes';
import type {ManifestScene} from '../src/contracts';
import {smoothProgress, storyState} from '../src/VisualStoryScene';

describe('WB mobile readability and causal timing', () => {
  it('preserves a complete authored image headline through the renderer', () => {
    const text = 'Extra income can disappear into new expenses before you notice';
    const scene = {headline: text, caption: 'A different narrated sentence.'} as ManifestScene;
    expect(imageHeadlineFor(scene)).toBe(text.toUpperCase());
    const layout = fitText(imageHeadlineFor(scene), 936, 56, 28, 3, (t,s) => t.length*s*.6);
    expect(layout.lines.join(' ')).toBe(text.toUpperCase());
    expect(layout.lines.length).toBeLessThanOrEqual(3);
  });
  it('wraps a long label without clipping or omitting letters', () => {
    const layout = fitText('NEW EXPENSES', 210, 34, 30, 2, (text,size) => text.length*size*.6);
    expect(layout.lines).toEqual(['NEW','EXPENSES']);
    expect(() => fitText('UNBREAKABLYLONGUNREADABLELABEL', 100, 34, 30, 2, (t,s)=>t.length*s*.6)).toThrow();
  });
  it('keeps all words, currency units, and clears captions through pauses', () => {
    const words = ['Set','aside','an','affordable','20','dollars','for','your','goal.'].map((text,i)=>({text,start_sec:i*.3,end_sec:i*.3+.25}));
    const cues = captionCuesFromWords(words);
    expect(cues.flatMap(c=>c.words).map(w=>w.text)).toEqual(words.map(w=>w.text));
    expect(cues.some(c=>c.text.includes('20 dollars'))).toBe(true);
    const paused = captionCuesFromWords([{text:'First.',start_sec:0,end_sec:1},{text:'Later.',start_sec:2,end_sec:3}]);
    expect(activeCaptionText(paused,1.5)).toBe('');
    expect(DEFAULT_CAPTION_STYLE).toBe('plain_phrase');
    expect(CAPTION_MIN_FONT_PX).toBeGreaterThanOrEqual(72);
    expect(CAPTION_MAX_WORDS).toBeLessThanOrEqual(8);
    expect(cues.every((cue) => cue.words.length <= CAPTION_MAX_WORDS + 1)).toBe(true);
  });
  it('has no overshoot and holds the completed action', () => {
    expect(smoothProgress(0,1,.65)).toBe(0);
    expect(smoothProgress(1.325,1,.65)).toBeCloseTo(.5);
    expect(smoothProgress(10,1,.65)).toBe(1);
    expect(storyState('costs').saving).toBe(false);
    expect(storyState('choice').saving).toBe(true);
    expect(storyState('goal').goal).toBe(true);
  });
  it('does not hide split_screen underlay when a semantic overlay is present', () => {
    expect(meaningDiagramReplacesUnderlay('icon_explainer', true)).toBe(true);
    expect(meaningDiagramReplacesUnderlay('split_screen', true)).toBe(false);
    expect(meaningDiagramReplacesUnderlay('icon_explainer', false)).toBe(false);
  });
  it('frames real production backgrounds as complete scenes and preserves icon/diagram layouts', () => {
    const scene = {
      scene_id: 's01', scene_type: 'ImageScene', timing: {start_sec: 0, end_sec: 5},
      narration: 'Extra income arrives.', caption: 'Extra income arrives.', layout_id: 'icon_explainer',
      assets: [{asset_id: 'bg', role: 'selected_image', path: 'scene.png', exists: true,
        provenance: {generation_parameters: {production_role: 'background', fit: 'cover'}}}],
    } as ManifestScene;
    expect(usesBackgroundIllustrationFrame(scene)).toBe(true);
    expect(usesBackgroundIllustrationFrame({...scene, semantic_overlay_kind: 'raise_absorption'})).toBe(false);
    expect(usesBackgroundIllustrationFrame({...scene, assets: [{...scene.assets[0],
      provenance: {generation_parameters: {production_role: 'icon', fit: 'contain'}}}]})).toBe(false);
    expect(usesBackgroundIllustrationFrame({...scene, assets: []})).toBe(false);
  });
});
