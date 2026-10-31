/** Wealth Blueprint native vector DataViz contracts — never AI bitmaps. */

export const CHART_TYPES = [
  'BAR_CHART',
  'LINE_CHART',
  'AREA_CHART',
  'DONUT_CHART',
  'COMPARISON',
  'TIMELINE',
  'MONEY_COUNTER',
  'PERCENTAGE_COUNTER',
  'COMPOUND_GROWTH',
  'CASHFLOW',
  'PORTFOLIO_ALLOCATION',
] as const;

export type ChartType = (typeof CHART_TYPES)[number];

export type NumberFormatMode = 'compact' | 'full';

export type ChartDatum = {
  label: string;
  value: number;
  color?: string;
};

export type TimelineEvent = {
  label: string;
  value?: number;
  at: string;
};

export type ComparisonSide = {
  label: string;
  value: number;
  tone?: 'positive' | 'negative' | 'neutral';
};

export type ChartTiming = {
  /** Seconds for data reveal (bars/lines/arcs). */
  dataEnterDuration?: number;
  /** Delay before labels appear. */
  labelDelay?: number;
  /** Label fade duration. */
  labelDuration?: number;
  /** Extra effect (pulse / highlight) delay. */
  effectDelay?: number;
  /** Stagger between series items in seconds. */
  stagger?: number;
};

export type ChartSpecBase = {
  type: ChartType;
  title: string;
  subtitle?: string;
  format?: NumberFormatMode;
  currency?: boolean;
  unit?: string;
  timing?: ChartTiming;
};

export type BarChartSpec = ChartSpecBase & {
  type: 'BAR_CHART';
  data: ChartDatum[];
};

export type LineChartSpec = ChartSpecBase & {
  type: 'LINE_CHART';
  data: ChartDatum[];
};

export type AreaChartSpec = ChartSpecBase & {
  type: 'AREA_CHART';
  data: ChartDatum[];
};

export type DonutChartSpec = ChartSpecBase & {
  type: 'DONUT_CHART';
  data: ChartDatum[];
};

export type ComparisonSpec = ChartSpecBase & {
  type: 'COMPARISON';
  left: ComparisonSide;
  right: ComparisonSide;
};

export type TimelineSpec = ChartSpecBase & {
  type: 'TIMELINE';
  events: TimelineEvent[];
};

export type MoneyCounterSpec = ChartSpecBase & {
  type: 'MONEY_COUNTER';
  value: number;
  prefix?: string;
};

export type PercentageCounterSpec = ChartSpecBase & {
  type: 'PERCENTAGE_COUNTER';
  value: number;
};

export type CompoundGrowthSpec = ChartSpecBase & {
  type: 'COMPOUND_GROWTH';
  /** Year labels with portfolio values. */
  data: ChartDatum[];
  contributionLabel?: string;
};

export type CashflowSpec = ChartSpecBase & {
  type: 'CASHFLOW';
  income: ChartDatum[];
  expenses: ChartDatum[];
};

export type PortfolioAllocationSpec = ChartSpecBase & {
  type: 'PORTFOLIO_ALLOCATION';
  data: ChartDatum[];
};

export type ChartSpec =
  | BarChartSpec
  | LineChartSpec
  | AreaChartSpec
  | DonutChartSpec
  | ComparisonSpec
  | TimelineSpec
  | MoneyCounterSpec
  | PercentageCounterSpec
  | CompoundGrowthSpec
  | CashflowSpec
  | PortfolioAllocationSpec;

export type DataVizTheme = {
  background: string;
  panel: string;
  accent: string;
  accentSoft: string;
  text: string;
  muted: string;
  danger: string;
  warn: string;
  series: string[];
  wordmark: string;
};
