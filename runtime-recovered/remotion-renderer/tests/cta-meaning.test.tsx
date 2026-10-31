import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it, vi} from 'vitest';
vi.mock('remotion', async (original) => ({
  ...await original<typeof import('remotion')>(),
  useCurrentFrame: () => 45,
  useVideoConfig: () => ({durationInFrames:225,fps:30,width:1080,height:1920}),
}));
// Production text is measured after fonts load in a browser. SSR must expose
// requested copy to catch internal labels; it cannot verify browser fit.
vi.mock('../src/typography', () => ({
  MeasuredText: ({text}: {text: string}) => <span data-measured-text="true">{text}</span>,
}));
import {CTAScene} from '../src/scenes';
import {DEFAULT_BRAND, type ManifestScene} from '../src/contracts';

describe('CTA preserves the actual topic', () => {
  it('does not turn a reserve closing into a payday allocation instruction', () => {
    const scene = {scene_id:'s06', headline:'Start Building Now', caption:'', narration:'Follow Wealth Blueprint. Build wealth on purpose.', timing:{start_sec:36,end_sec:44}, caption_words:[]} as unknown as ManifestScene;
    const html = renderToStaticMarkup(<CTAScene scene={scene} brand={DEFAULT_BRAND} />);
    expect(html).toContain('FOLLOW');
    expect(html).toContain('Build wealth on purpose.');
    expect(html).not.toMatch(/EVERY PAYDAY|KEEP A SLICE|REPEAT|KEEP THE RAISE/);
  });
  it('retains the supported raise-specific closing when explicitly selected', () => {
    const scene = {scene_id:'s06', headline:'Keep the raise', caption:'', semantic_overlay_kind:'raise_absorption', timing:{start_sec:36,end_sec:44}, caption_words:[]} as unknown as ManifestScene;
    const html = renderToStaticMarkup(<CTAScene scene={scene} brand={DEFAULT_BRAND} />);
    expect(html).toContain('OLD PAY');
    expect(html).toContain('SPEND THE REST');
  });
  it.each(['wb_planning_desk_v1', 'wb_planning_desk_painted_v1'] as const)(
    'uses one deliberate branded ending for the explicit %s selection', (illustrationSystem) => {
      const scene = {scene_id:'s06', headline:'CTA: Follow Wealth Blueprint', caption:'',
        narration:'Follow Wealth Blueprint. Build wealth on purpose.', timing:{start_sec:34,end_sec:37.2},
        caption_words:[]} as unknown as ManifestScene;
      const html = renderToStaticMarkup(<CTAScene scene={scene} brand={{...DEFAULT_BRAND, illustrationSystem}} />);
      expect(html).toContain('data-wb-cta="planning-desk-closing"');
      expect(html.match(/WEALTH BLUEPRINT/g)).toHaveLength(1);
      expect(html).toContain('FOLLOW');
      expect(html).toContain('Build wealth on purpose.');
      expect(html).not.toContain('CTA:');
      expect(html).not.toContain('WITH INTENTION');
      expect(html).not.toContain('SAVE THIS IDEA');
      expect(html).not.toContain('PERSONAL FINANCE');
    },
  );
  it('keeps default closing copy and does not opt in from the topic alone', () => {
    const scene = {scene_id:'s06', headline:'Start Building Now', caption:'',
      narration:'Review your planning desk.', timing:{start_sec:34,end_sec:37.2}, caption_words:[]} as unknown as ManifestScene;
    const html = renderToStaticMarkup(<CTAScene scene={scene} brand={DEFAULT_BRAND} />);
    expect(html).not.toContain('data-wb-cta="planning-desk-closing"');
    expect(html).toContain('WITH INTENTION');
    expect(html).toContain('START BUILDING NOW');
    expect(html).toContain('SAVE THIS IDEA');
    expect(html).toContain('PERSONAL FINANCE');
  });
  it('retains timed plain-phrase captions underneath the selected branded closing', () => {
    const scene = {scene_id:'s06', headline:'CTA: Follow Wealth Blueprint', caption:'',
      narration:'Follow Wealth Blueprint. Build wealth on purpose.', timing:{start_sec:34,end_sec:37.2},
      caption_words:[{text:'Build',start_sec:35,end_sec:35.6}, {text:'wealth',start_sec:35.6,end_sec:36},
        {text:'on',start_sec:36,end_sec:36.4}, {text:'purpose.',start_sec:36.4,end_sec:37}]} as unknown as ManifestScene;
    const html = renderToStaticMarkup(<CTAScene scene={scene}
      brand={{...DEFAULT_BRAND, captionStyle:'plain_phrase', illustrationSystem:'wb_planning_desk_painted_v1'}} />);
    expect(html).toContain('data-caption-style="plain_phrase"');
    expect(html).toContain('Build wealth on purpose.');
    expect(html.match(/WEALTH BLUEPRINT/g)).toHaveLength(1);
    expect(html).not.toContain('CTA:');
  });
  it('preserves the raise-specific closing even with an explicit desk brand selection', () => {
    const scene = {scene_id:'s06', headline:'Keep the raise', caption:'', semantic_overlay_kind:'raise_absorption',
      timing:{start_sec:36,end_sec:44}, caption_words:[]} as unknown as ManifestScene;
    const html = renderToStaticMarkup(<CTAScene scene={scene}
      brand={{...DEFAULT_BRAND, illustrationSystem:'wb_planning_desk_painted_v1'}} />);
    expect(html).toContain('OLD PAY');
    expect(html).toContain('SPEND THE REST');
    expect(html).not.toContain('data-wb-cta="planning-desk-closing"');
  });
});
