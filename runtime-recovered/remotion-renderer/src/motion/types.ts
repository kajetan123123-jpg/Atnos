/**
 * Deterministic motion-design presets for Atnos Remotion.
 * Atnos selects preset id + parameters; it never generates animation code.
 */

export const ANIMATION_PRESET_IDS = [
  'TEXT_FADE',
  'TEXT_SLIDE_UP',
  'TEXT_SLIDE_LEFT',
  'TEXT_POP',
  'TEXT_REVEAL',
  'NUMBER_COUNT',
  'ICON_POP',
  'ICON_FLOAT',
  'ASSET_SLIDE',
  'ASSET_SCALE',
  'CHARACTER_ENTER_LEFT',
  'CHARACTER_ENTER_RIGHT',
  'CHARACTER_IDLE_FLOAT',
  'CHART_GROW',
  'LINE_DRAW',
  'BAR_GROW',
  'HIGHLIGHT',
  'CAMERA_PUSH',
  'PARALLAX',
  'SHAKE_LIGHT',
  'IMPACT',
  'MASK_REVEAL',
] as const;

export type AnimationPresetId = (typeof ANIMATION_PRESET_IDS)[number];

export type EasingName =
  | 'linear'
  | 'ease_in'
  | 'ease_out'
  | 'ease_in_out'
  | 'ease_out_cubic'
  | 'ease_out_back';

export type MotionDirection = 'up' | 'down' | 'left' | 'right' | 'in' | 'out';

export type AnimationCategory =
  | 'text'
  | 'number'
  | 'icon'
  | 'asset'
  | 'character'
  | 'chart'
  | 'camera'
  | 'fx';

/** Parameters Atnos (or a layout) may override per use. */
export type AnimationParams = {
  duration: number;
  delay: number;
  easing: EasingName;
  intensity?: number;
  direction?: MotionDirection;
};

export type ResolvedAnimationParams = {
  duration: number;
  delay: number;
  easing: EasingName;
  intensity: number;
  direction: MotionDirection;
};

/** Frame sample consumed by UI / charts — never remotion keyframes copied around. */
export type MotionSample = {
  opacity: number;
  translateX: number;
  translateY: number;
  scale: number;
  rotate: number;
  /** 0–1 for count / chart / reveal drivers */
  progress: number;
  clipProgress: number;
};

export type PresetDefinition = {
  id: AnimationPresetId;
  label: string;
  category: AnimationCategory;
  defaults: ResolvedAnimationParams;
  evaluate: (t: number, params: ResolvedAnimationParams) => MotionSample;
};

export const REST_SAMPLE: MotionSample = {
  opacity: 1,
  translateX: 0,
  translateY: 0,
  scale: 1,
  rotate: 0,
  progress: 1,
  clipProgress: 1,
};

export const HIDDEN_SAMPLE: MotionSample = {
  opacity: 0,
  translateX: 0,
  translateY: 0,
  scale: 1,
  rotate: 0,
  progress: 0,
  clipProgress: 0,
};
