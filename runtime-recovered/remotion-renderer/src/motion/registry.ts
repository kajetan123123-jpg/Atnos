import type {CSSProperties} from 'react';
import {applyEasing, clamp01, lerp} from './easings';
import {
  ANIMATION_PRESET_IDS,
  HIDDEN_SAMPLE,
  REST_SAMPLE,
  type AnimationParams,
  type AnimationPresetId,
  type MotionSample,
  type PresetDefinition,
  type ResolvedAnimationParams,
} from './types';

const base = (partial: Partial<MotionSample>): MotionSample => ({
  ...REST_SAMPLE,
  ...partial,
});

const fadeIn = (t: number): MotionSample => base({opacity: t, progress: t, clipProgress: t});

const slide = (t: number, distance: number, axis: 'x' | 'y', fromNegative: boolean): MotionSample => {
  const from = fromNegative ? -distance : distance;
  const value = lerp(from, 0, t);
  return base({
    opacity: clamp01(t * 1.15),
    translateX: axis === 'x' ? value : 0,
    translateY: axis === 'y' ? value : 0,
    progress: t,
    clipProgress: t,
  });
};

const PRESETS: Record<AnimationPresetId, PresetDefinition> = {
  TEXT_FADE: {
    id: 'TEXT_FADE',
    label: 'Text Fade',
    category: 'text',
    defaults: {duration: 0.55, delay: 0, easing: 'ease_out', intensity: 0.45, direction: 'in'},
    evaluate: (t) => fadeIn(t),
  },
  TEXT_SLIDE_UP: {
    id: 'TEXT_SLIDE_UP',
    label: 'Text Slide Up',
    category: 'text',
    defaults: {duration: 0.6, delay: 0, easing: 'ease_out_cubic', intensity: 0.5, direction: 'up'},
    evaluate: (t, p) => slide(t, 28 + p.intensity * 40, 'y', false),
  },
  TEXT_SLIDE_LEFT: {
    id: 'TEXT_SLIDE_LEFT',
    label: 'Text Slide Left',
    category: 'text',
    defaults: {duration: 0.6, delay: 0, easing: 'ease_out_cubic', intensity: 0.5, direction: 'left'},
    evaluate: (t, p) => slide(t, 36 + p.intensity * 48, 'x', false),
  },
  TEXT_POP: {
    id: 'TEXT_POP',
    label: 'Text Pop',
    category: 'text',
    defaults: {duration: 0.5, delay: 0, easing: 'ease_out_back', intensity: 0.4, direction: 'in'},
    evaluate: (t, p) =>
      base({
        opacity: clamp01(t * 1.4),
        scale: lerp(0.86, 1, t) + (t > 0.85 ? (1 - t) * p.intensity * 0.08 : 0),
        progress: t,
        clipProgress: t,
      }),
  },
  TEXT_REVEAL: {
    id: 'TEXT_REVEAL',
    label: 'Text Reveal',
    category: 'text',
    defaults: {duration: 0.7, delay: 0, easing: 'ease_in_out', intensity: 0.5, direction: 'up'},
    evaluate: (t) => base({opacity: 1, progress: t, clipProgress: t, translateY: lerp(12, 0, t)}),
  },
  NUMBER_COUNT: {
    id: 'NUMBER_COUNT',
    label: 'Number Count',
    category: 'number',
    defaults: {duration: 1.1, delay: 0, easing: 'ease_out_cubic', intensity: 0.55, direction: 'in'},
    evaluate: (t) =>
      base({
        opacity: clamp01(0.35 + t),
        scale: lerp(0.94, 1, t),
        progress: t,
        clipProgress: t,
      }),
  },
  ICON_POP: {
    id: 'ICON_POP',
    label: 'Icon Pop',
    category: 'icon',
    defaults: {duration: 0.45, delay: 0, easing: 'ease_out_back', intensity: 0.45, direction: 'in'},
    evaluate: (t, p) =>
      base({
        opacity: clamp01(t * 1.5),
        scale: lerp(0.7, 1, t) + Math.sin(t * Math.PI) * p.intensity * 0.06,
        progress: t,
        clipProgress: t,
      }),
  },
  ICON_FLOAT: {
    id: 'ICON_FLOAT',
    label: 'Icon Float',
    category: 'icon',
    defaults: {duration: 2.4, delay: 0, easing: 'ease_in_out', intensity: 0.35, direction: 'up'},
    evaluate: (t, p) => {
      const wave = Math.sin(t * Math.PI * 2);
      return base({
        opacity: 1,
        translateY: wave * (6 + p.intensity * 10),
        scale: 1 + wave * 0.012 * p.intensity,
        progress: t,
        clipProgress: 1,
      });
    },
  },
  ASSET_SLIDE: {
    id: 'ASSET_SLIDE',
    label: 'Asset Slide',
    category: 'asset',
    defaults: {duration: 0.7, delay: 0, easing: 'ease_out_cubic', intensity: 0.5, direction: 'left'},
    evaluate: (t, p) => {
      const distance = 48 + p.intensity * 70;
      const dir = p.direction;
      if (dir === 'up' || dir === 'down') {
        return slide(t, distance, 'y', dir === 'down');
      }
      return slide(t, distance, 'x', dir === 'right' || dir === 'in');
    },
  },
  ASSET_SCALE: {
    id: 'ASSET_SCALE',
    label: 'Asset Scale',
    category: 'asset',
    defaults: {duration: 0.65, delay: 0, easing: 'ease_out', intensity: 0.45, direction: 'in'},
    evaluate: (t, p) =>
      base({
        opacity: clamp01(t * 1.2),
        scale: lerp(0.88 - p.intensity * 0.06, 1, t),
        progress: t,
        clipProgress: t,
      }),
  },
  CHARACTER_ENTER_LEFT: {
    id: 'CHARACTER_ENTER_LEFT',
    label: 'Character Enter Left',
    category: 'character',
    defaults: {duration: 0.75, delay: 0, easing: 'ease_out_cubic', intensity: 0.55, direction: 'right'},
    evaluate: (t, p) =>
      base({
        opacity: clamp01(t * 1.25),
        translateX: lerp(-(90 + p.intensity * 80), 0, t),
        scale: lerp(0.96, 1, t),
        progress: t,
        clipProgress: t,
      }),
  },
  CHARACTER_ENTER_RIGHT: {
    id: 'CHARACTER_ENTER_RIGHT',
    label: 'Character Enter Right',
    category: 'character',
    defaults: {duration: 0.75, delay: 0, easing: 'ease_out_cubic', intensity: 0.55, direction: 'left'},
    evaluate: (t, p) =>
      base({
        opacity: clamp01(t * 1.25),
        translateX: lerp(90 + p.intensity * 80, 0, t),
        scale: lerp(0.96, 1, t),
        progress: t,
        clipProgress: t,
      }),
  },
  CHARACTER_IDLE_FLOAT: {
    id: 'CHARACTER_IDLE_FLOAT',
    label: 'Character Idle Float',
    category: 'character',
    defaults: {duration: 3.2, delay: 0, easing: 'ease_in_out', intensity: 0.3, direction: 'up'},
    evaluate: (t, p) => {
      const wave = Math.sin(t * Math.PI * 2);
      return base({
        opacity: 1,
        translateY: wave * (5 + p.intensity * 8),
        rotate: wave * p.intensity * 0.6,
        progress: t,
        clipProgress: 1,
      });
    },
  },
  CHART_GROW: {
    id: 'CHART_GROW',
    label: 'Chart Grow',
    category: 'chart',
    defaults: {duration: 1.0, delay: 0, easing: 'ease_out_cubic', intensity: 0.5, direction: 'up'},
    evaluate: (t) =>
      base({
        opacity: clamp01(0.4 + t),
        progress: t,
        clipProgress: t,
        translateY: lerp(18, 0, t),
      }),
  },
  LINE_DRAW: {
    id: 'LINE_DRAW',
    label: 'Line Draw',
    category: 'chart',
    defaults: {duration: 1.15, delay: 0, easing: 'ease_in_out', intensity: 0.5, direction: 'right'},
    evaluate: (t) => base({opacity: 1, progress: t, clipProgress: t}),
  },
  BAR_GROW: {
    id: 'BAR_GROW',
    label: 'Bar Grow',
    category: 'chart',
    defaults: {duration: 0.85, delay: 0, easing: 'ease_out_cubic', intensity: 0.5, direction: 'up'},
    evaluate: (t) => base({opacity: 1, progress: t, clipProgress: t}),
  },
  HIGHLIGHT: {
    id: 'HIGHLIGHT',
    label: 'Highlight',
    category: 'fx',
    defaults: {duration: 0.7, delay: 0, easing: 'ease_out', intensity: 0.55, direction: 'in'},
    evaluate: (t, p) =>
      base({
        opacity: lerp(0.35, 1, t),
        scale: lerp(1, 1 + p.intensity * 0.04, Math.sin(t * Math.PI)),
        progress: t,
        clipProgress: t,
      }),
  },
  CAMERA_PUSH: {
    id: 'CAMERA_PUSH',
    label: 'Camera Push',
    category: 'camera',
    defaults: {duration: 1.2, delay: 0, easing: 'ease_in_out', intensity: 0.4, direction: 'in'},
    evaluate: (t, p) =>
      base({
        opacity: 1,
        scale: lerp(1, 1 + 0.06 + p.intensity * 0.08, t),
        progress: t,
        clipProgress: 1,
      }),
  },
  PARALLAX: {
    id: 'PARALLAX',
    label: 'Parallax',
    category: 'camera',
    defaults: {duration: 1.4, delay: 0, easing: 'ease_in_out', intensity: 0.45, direction: 'left'},
    evaluate: (t, p) => {
      const amp = 16 + p.intensity * 28;
      const dir = p.direction === 'right' || p.direction === 'down' ? 1 : -1;
      const axisY = p.direction === 'up' || p.direction === 'down';
      return base({
        opacity: 1,
        translateX: axisY ? 0 : dir * lerp(0, amp, t),
        translateY: axisY ? dir * lerp(0, amp * 0.7, t) : 0,
        progress: t,
        clipProgress: 1,
      });
    },
  },
  SHAKE_LIGHT: {
    id: 'SHAKE_LIGHT',
    label: 'Shake Light',
    category: 'fx',
    defaults: {duration: 0.45, delay: 0, easing: 'ease_out', intensity: 0.35, direction: 'left'},
    evaluate: (t, p) => {
      const damp = 1 - t;
      const amp = (3 + p.intensity * 7) * damp;
      return base({
        opacity: 1,
        translateX: Math.sin(t * Math.PI * 8) * amp,
        translateY: Math.cos(t * Math.PI * 6) * amp * 0.35,
        progress: t,
        clipProgress: 1,
      });
    },
  },
  IMPACT: {
    id: 'IMPACT',
    label: 'Impact',
    category: 'fx',
    defaults: {duration: 0.4, delay: 0, easing: 'ease_out_back', intensity: 0.5, direction: 'in'},
    evaluate: (t, p) => {
      const punch = Math.sin(clamp01(t / 0.35) * Math.PI) * (1 - t);
      return base({
        opacity: 1,
        scale: 1 + punch * (0.04 + p.intensity * 0.06),
        translateY: punch * -(4 + p.intensity * 6),
        progress: t,
        clipProgress: 1,
      });
    },
  },
  MASK_REVEAL: {
    id: 'MASK_REVEAL',
    label: 'Mask Reveal',
    category: 'fx',
    defaults: {duration: 0.8, delay: 0, easing: 'ease_in_out', intensity: 0.5, direction: 'up'},
    evaluate: (t) => base({opacity: 1, progress: t, clipProgress: t}),
  },
};

const LOOPING: ReadonlySet<AnimationPresetId> = new Set(['ICON_FLOAT', 'CHARACTER_IDLE_FLOAT']);

function resolveParams(
  preset: PresetDefinition,
  overrides: Partial<AnimationParams> = {},
): ResolvedAnimationParams {
  return {
    duration: overrides.duration ?? preset.defaults.duration,
    delay: overrides.delay ?? preset.defaults.delay,
    easing: overrides.easing ?? preset.defaults.easing,
    intensity: clamp01(overrides.intensity ?? preset.defaults.intensity),
    direction: overrides.direction ?? preset.defaults.direction,
  };
}

export function listAnimationPresets(): readonly AnimationPresetId[] {
  return ANIMATION_PRESET_IDS;
}

export function getAnimationPreset(id: string): PresetDefinition {
  if (!(ANIMATION_PRESET_IDS as readonly string[]).includes(id)) {
    throw new Error(`Unknown animation preset: ${id}`);
  }
  return PRESETS[id as AnimationPresetId];
}

export function isAnimationPresetId(value: string): value is AnimationPresetId {
  return (ANIMATION_PRESET_IDS as readonly string[]).includes(value);
}

export function animationPresetCatalog(): Array<{
  id: AnimationPresetId;
  label: string;
  category: string;
  defaults: ResolvedAnimationParams;
}> {
  return ANIMATION_PRESET_IDS.map((id) => {
    const preset = PRESETS[id];
    return {
      id: preset.id,
      label: preset.label,
      category: preset.category,
      defaults: {...preset.defaults},
    };
  });
}

export function sampleAnimation(
  presetId: AnimationPresetId,
  frame: number,
  fps: number,
  overrides: Partial<AnimationParams> = {},
): MotionSample {
  const preset = getAnimationPreset(presetId);
  const params = resolveParams(preset, overrides);
  if (!(params.duration > 0)) {
    throw new Error(`Animation ${presetId} requires positive duration`);
  }
  const startFrame = Math.round(params.delay * fps);
  const durationFrames = Math.max(1, Math.round(params.duration * fps));
  const local = (frame - startFrame) / durationFrames;
  if (local <= 0) {
    return LOOPING.has(presetId) ? REST_SAMPLE : {...HIDDEN_SAMPLE};
  }
  if (!LOOPING.has(presetId) && local >= 1) {
    return preset.evaluate(1, params);
  }
  const phase = LOOPING.has(presetId) ? ((local % 1) + 1) % 1 : clamp01(local);
  if (LOOPING.has(presetId)) {
    return preset.evaluate(phase, params);
  }
  return preset.evaluate(applyEasing(params.easing, phase), params);
}

export function motionToCss(sample: MotionSample): CSSProperties {
  return {
    opacity: sample.opacity,
    transform: `translate(${sample.translateX}px, ${sample.translateY}px) scale(${sample.scale}) rotate(${sample.rotate}deg)`,
    transformOrigin: 'center center',
  };
}

export function maskRevealClip(sample: MotionSample, direction: ResolvedAnimationParams['direction']): string {
  const p = clamp01(sample.clipProgress) * 100;
  switch (direction) {
    case 'left':
      return `inset(0 ${100 - p}% 0 0)`;
    case 'right':
      return `inset(0 0 0 ${100 - p}%)`;
    case 'down':
      return `inset(0 0 ${100 - p}% 0)`;
    case 'up':
    default:
      return `inset(${100 - p}% 0 0 0)`;
  }
}
