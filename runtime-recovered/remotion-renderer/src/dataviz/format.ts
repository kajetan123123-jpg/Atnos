import type {NumberFormatMode} from './types';

export type FormatNumberOptions = {
  mode?: NumberFormatMode;
  currency?: boolean;
  unit?: string;
  maximumFractionDigits?: number;
  /** Force exact digits even in compact mode (e.g. counters mid-animation). */
  forceFull?: boolean;
};

/**
 * 1000 → 1K, 1_000_000 → 1M, with optional full value.
 * Handles tiny and large magnitudes without clipping semantics.
 */
export const formatNumber = (value: number, options: FormatNumberOptions = {}): string => {
  if (!Number.isFinite(value)) return '—';
  const mode = options.forceFull ? 'full' : options.mode ?? 'compact';
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : '';
  const currency = options.currency ? '$' : '';
  const unit = options.unit ? ` ${options.unit}` : '';

  if (mode === 'full' || abs < 1000) {
    const digits = options.maximumFractionDigits ?? (abs > 0 && abs < 1 ? 2 : abs < 10 && !Number.isInteger(value) ? 2 : 0);
    const body = abs.toLocaleString('en-US', {
      maximumFractionDigits: digits,
      minimumFractionDigits: 0,
    });
    return `${sign}${currency}${body}${unit}`;
  }

  const tiers: Array<{limit: number; suffix: string; div: number}> = [
    {limit: 1e12, suffix: 'T', div: 1e12},
    {limit: 1e9, suffix: 'B', div: 1e9},
    {limit: 1e6, suffix: 'M', div: 1e6},
    {limit: 1e3, suffix: 'K', div: 1e3},
  ];
  const tier = tiers.find((item) => abs >= item.limit) ?? tiers[tiers.length - 1];
  const scaled = abs / tier.div;
  const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
  const trimmed = Number(scaled.toFixed(digits)).toString();
  return `${sign}${currency}${trimmed}${tier.suffix}${unit}`;
};

export const formatPercent = (value: number, options: {mode?: NumberFormatMode; forceFull?: boolean} = {}): string => {
  if (!Number.isFinite(value)) return '—';
  if (options.forceFull || options.mode === 'full') {
    return `${value.toLocaleString('en-US', {maximumFractionDigits: 2})}%`;
  }
  if (Math.abs(value) >= 1000) return `${formatNumber(value, {mode: 'compact'})}%`;
  const digits = Math.abs(value) < 10 && !Number.isInteger(value) ? 1 : 0;
  return `${Number(value.toFixed(digits))}%`;
};
