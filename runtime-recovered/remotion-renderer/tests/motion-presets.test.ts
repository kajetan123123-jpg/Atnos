import {describe, expect, it} from 'vitest';
import {
  ANIMATION_PRESET_IDS,
  animationPresetCatalog,
  getAnimationPreset,
  isAnimationPresetId,
  listAnimationPresets,
  sampleAnimation,
} from '../src/motion';

describe('animation preset registry', () => {
  it('registers every required professional preset exactly once', () => {
    expect(listAnimationPresets()).toEqual([...ANIMATION_PRESET_IDS]);
    expect(listAnimationPresets()).toHaveLength(22);
    expect(new Set(listAnimationPresets()).size).toBe(22);
  });

  it('exposes duration, delay, easing, intensity and direction defaults', () => {
    for (const id of listAnimationPresets()) {
      const preset = getAnimationPreset(id);
      expect(preset.defaults.duration).toBeGreaterThan(0);
      expect(preset.defaults.delay).toBeGreaterThanOrEqual(0);
      expect(preset.defaults.easing).toBeTruthy();
      expect(preset.defaults.intensity).toBeGreaterThanOrEqual(0);
      expect(preset.defaults.intensity).toBeLessThanOrEqual(1);
      expect(preset.defaults.direction).toBeTruthy();
    }
  });

  it('rejects unknown presets so Atnos cannot invent animation code ids', () => {
    expect(isAnimationPresetId('TEXT_FADE')).toBe(true);
    expect(isAnimationPresetId('WIGGLE_RANDOM')).toBe(false);
    expect(() => getAnimationPreset('WIGGLE_RANDOM')).toThrow(/Unknown animation preset/);
  });

  it('samples deterministically for the same frame', () => {
    const a = sampleAnimation('TEXT_SLIDE_UP', 12, 30);
    const b = sampleAnimation('TEXT_SLIDE_UP', 12, 30);
    expect(a).toEqual(b);
    expect(a.translateY).toBeGreaterThan(0);
    expect(a.opacity).toBeGreaterThan(0);
  });

  it('keeps number/chart progress in 0..1 and holds the end pose', () => {
    const mid = sampleAnimation('NUMBER_COUNT', 15, 30);
    const end = sampleAnimation('NUMBER_COUNT', 90, 30);
    expect(mid.progress).toBeGreaterThan(0);
    expect(mid.progress).toBeLessThan(1);
    expect(end.progress).toBe(1);
    expect(end.opacity).toBe(1);
  });

  it('exports a catalog without evaluate functions for Atnos selection', () => {
    const catalog = animationPresetCatalog();
    expect(catalog).toHaveLength(22);
    expect(catalog[0]).toMatchObject({
      id: 'TEXT_FADE',
      label: 'Text Fade',
      category: 'text',
    });
    expect(catalog[0]).not.toHaveProperty('evaluate');
  });
});
