import type {EasingName} from './types';

/** Smoothstep-style curves — natural motion, light overshoot only via ease_out_back. */
export function applyEasing(name: EasingName, t: number): number {
  const x = clamp01(t);
  switch (name) {
    case 'linear':
      return x;
    case 'ease_in':
      return x * x;
    case 'ease_out':
      return 1 - (1 - x) * (1 - x);
    case 'ease_in_out':
      return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
    case 'ease_out_cubic':
      return 1 - Math.pow(1 - x, 3);
    case 'ease_out_back': {
      const c1 = 1.40158;
      const c3 = c1 + 1;
      return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
    }
    default:
      return x;
  }
}

export function clamp01(value: number): number {
  if (value <= 0) return 0;
  if (value >= 1) return 1;
  return value;
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}
