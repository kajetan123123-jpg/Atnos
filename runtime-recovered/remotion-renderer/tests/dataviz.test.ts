import {describe, expect, it} from 'vitest';
import {formatNumber, formatPercent} from '../src/dataviz/format';
import {CHART_TYPES, listDataVizDemoSpecs} from '../src/dataviz';
import {niceDomain} from '../src/dataviz/timing';

describe('formatNumber', () => {
  it('compacts thousands and millions', () => {
    expect(formatNumber(1000)).toBe('1K');
    expect(formatNumber(1_000_000)).toBe('1M');
    expect(formatNumber(1500)).toBe('1.5K');
    expect(formatNumber(2_500_000)).toBe('2.5M');
  });

  it('keeps full values when requested', () => {
    expect(formatNumber(1000, {mode: 'full'})).toBe('1,000');
    expect(formatNumber(1_284_500, {mode: 'full', currency: true})).toBe('$1,284,500');
  });

  it('handles tiny and negative values', () => {
    expect(formatNumber(0.42, {mode: 'full'})).toBe('0.42');
    expect(formatNumber(-1200)).toBe('-1.2K');
  });
});

describe('formatPercent', () => {
  it('formats percentages', () => {
    expect(formatPercent(22.5, {mode: 'full'})).toBe('22.5%');
    expect(formatPercent(8)).toBe('8%');
  });
});

describe('dataviz demos', () => {
  it('covers every chart type', () => {
    const specs = listDataVizDemoSpecs();
    expect(specs).toHaveLength(CHART_TYPES.length);
    expect(new Set(specs.map((spec) => spec.type))).toEqual(new Set(CHART_TYPES));
  });

  it('builds a nice domain for mixed magnitudes', () => {
    const domain = niceDomain([0.5, 1.2, 3]);
    expect(domain.min).toBeLessThanOrEqual(0.5);
    expect(domain.max).toBeGreaterThanOrEqual(3);
  });
});
