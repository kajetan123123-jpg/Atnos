import {sampleAnimation} from '../motion';
import type {ChartTiming} from './types';

export const DEFAULT_TIMING: Required<ChartTiming> = {
  dataEnterDuration: 0.85,
  labelDelay: 0.35,
  labelDuration: 0.45,
  effectDelay: 0.55,
  stagger: 0.08,
};

export const resolveTiming = (timing?: ChartTiming): Required<ChartTiming> => ({
  ...DEFAULT_TIMING,
  ...timing,
});

export const progressAt = (
  frame: number,
  fps: number,
  options: {
    delaySec: number;
    durationSec: number;
    preset?: 'BAR_GROW' | 'LINE_DRAW' | 'CHART_GROW' | 'NUMBER_COUNT' | 'FADE_IN';
  },
): number => {
  const delaySec = options.delaySec;
  const durationSec = options.durationSec;
  const preset = options.preset ?? 'CHART_GROW';
  const local = Math.max(0, frame - Math.round(delaySec * fps));
  if (preset === 'FADE_IN') {
    const t = Math.min(1, local / Math.max(1, durationSec * fps));
    return t * t * (3 - 2 * t);
  }
  return sampleAnimation(preset, local, fps, {duration: durationSec, delay: 0}).progress;
};

export const labelOpacity = (frame: number, fps: number, timing: Required<ChartTiming>): number =>
  progressAt(frame, fps, {
    delaySec: timing.labelDelay,
    durationSec: timing.labelDuration,
    preset: 'FADE_IN',
  });

/** Nice tick range that works for tiny and huge magnitudes. */
export const niceDomain = (values: number[], padding = 0.08): {min: number; max: number} => {
  if (!values.length) return {min: 0, max: 1};
  let min = Math.min(...values, 0);
  let max = Math.max(...values, 0);
  if (min === max) {
    if (min === 0) return {min: 0, max: 1};
    const pad = Math.abs(min) * 0.15;
    return {min: min - pad, max: max + pad};
  }
  const span = max - min;
  min -= span * padding;
  max += span * padding;
  if (min > 0 && min < span * 0.2) min = 0;
  return {min, max};
};
