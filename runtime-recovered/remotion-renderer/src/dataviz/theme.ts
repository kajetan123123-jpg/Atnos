import type {BrandSpec} from '../contracts';
import {DEFAULT_BRAND} from '../contracts';
import type {DataVizTheme} from './types';

/** Channel-aligned tokens with Remotion BrandSpec fallback. */
export const DATAVIZ_THEME: DataVizTheme = {
  background: '#111111',
  panel: '#1C1C1E',
  accent: '#39FF9F',
  accentSoft: '#0F5257',
  text: '#F5F5F5',
  muted: '#9CA8A2',
  danger: '#D35A63',
  warn: '#D5AD6C',
  series: ['#39FF9F', '#4B7977', '#D5AD6C', '#426579', '#C18A49', '#89A9D6', '#995E4B'],
  wordmark: 'WEALTH BLUEPRINT',
};

export const themeFromBrand = (brand: BrandSpec | Partial<BrandSpec> = DEFAULT_BRAND): DataVizTheme => ({
  ...DATAVIZ_THEME,
  background: brand.background ?? DATAVIZ_THEME.background,
  panel: brand.panel ?? DATAVIZ_THEME.panel,
  accent: brand.accent ?? DATAVIZ_THEME.accent,
  accentSoft: brand.accentSoft ?? DATAVIZ_THEME.accentSoft,
  text: brand.text ?? DATAVIZ_THEME.text,
  muted: brand.muted ?? DATAVIZ_THEME.muted,
  wordmark: brand.wordmark ?? DATAVIZ_THEME.wordmark,
});

export const seriesColor = (theme: DataVizTheme, index: number, override?: string): string =>
  override ?? theme.series[index % theme.series.length];
