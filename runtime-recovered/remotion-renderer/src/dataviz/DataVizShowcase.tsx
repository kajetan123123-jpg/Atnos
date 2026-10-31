import React from 'react';
import {AbsoluteFill, Sequence} from 'remotion';
import {DataVizScene} from './DataVizScene';
import {CHART_TYPES, type ChartSpec, type ChartType} from './types';
import {DATAVIZ_THEME} from './theme';

export const DATAVIZ_SHOWCASE_FPS = 30;
export const DATAVIZ_SHOWCASE_WIDTH = 1080;
export const DATAVIZ_SHOWCASE_HEIGHT = 1920;
export const DATAVIZ_SEGMENT_FRAMES = 72;
export const DATAVIZ_SHOWCASE_COMPOSITION_ID = 'WealthBlueprintDataVizShowcase';

export const dataVizShowcaseDurationInFrames = (): number => CHART_TYPES.length * DATAVIZ_SEGMENT_FRAMES;

const demoSpecs: Record<ChartType, ChartSpec> = {
  BAR_CHART: {
    type: 'BAR_CHART',
    title: 'Monthly Cash Flow',
    subtitle: 'Income vs expenses',
    format: 'compact',
    currency: true,
    data: [
      {label: 'Income', value: 8000},
      {label: 'Expenses', value: 6200},
      {label: 'Invested', value: 1200},
    ],
  },
  LINE_CHART: {
    type: 'LINE_CHART',
    title: 'Savings Balance',
    format: 'compact',
    currency: true,
    data: [
      {label: 'Jan', value: 1200},
      {label: 'Mar', value: 2400},
      {label: 'May', value: 3900},
      {label: 'Jul', value: 5100},
      {label: 'Sep', value: 6800},
    ],
  },
  AREA_CHART: {
    type: 'AREA_CHART',
    title: 'Emergency Fund',
    format: 'compact',
    currency: true,
    data: [
      {label: 'M1', value: 500},
      {label: 'M2', value: 1100},
      {label: 'M3', value: 1900},
      {label: 'M4', value: 2800},
      {label: 'M5', value: 4000},
    ],
  },
  DONUT_CHART: {
    type: 'DONUT_CHART',
    title: 'Budget Split',
    format: 'compact',
    currency: true,
    data: [
      {label: 'Needs', value: 4000},
      {label: 'Wants', value: 1800},
      {label: 'Savings', value: 1200},
    ],
  },
  COMPARISON: {
    type: 'COMPARISON',
    title: 'Keep vs Spend',
    format: 'compact',
    currency: true,
    left: {label: 'Kept', value: 1800, tone: 'positive'},
    right: {label: 'Spent away', value: 6200, tone: 'negative'},
  },
  TIMELINE: {
    type: 'TIMELINE',
    title: 'Wealth Path',
    format: 'compact',
    currency: true,
    events: [
      {at: 'Year 1', label: 'Start investing', value: 3000},
      {at: 'Year 5', label: 'Habit locked', value: 28000},
      {at: 'Year 10', label: 'Compounding', value: 78000},
      {at: 'Year 20', label: 'Freedom gap', value: 245000},
    ],
  },
  MONEY_COUNTER: {
    type: 'MONEY_COUNTER',
    title: 'Net Worth Snapshot',
    value: 1284500,
    format: 'compact',
    currency: true,
  },
  PERCENTAGE_COUNTER: {
    type: 'PERCENTAGE_COUNTER',
    title: 'Savings Rate',
    value: 22.5,
    format: 'full',
  },
  COMPOUND_GROWTH: {
    type: 'COMPOUND_GROWTH',
    title: 'Compound Growth',
    subtitle: 'Consistent monthly investing',
    format: 'compact',
    currency: true,
    data: [
      {label: 'Y0', value: 0},
      {label: 'Y5', value: 42000},
      {label: 'Y10', value: 112000},
      {label: 'Y20', value: 410000},
      {label: 'Y30', value: 1_150_000},
    ],
  },
  CASHFLOW: {
    type: 'CASHFLOW',
    title: 'Cashflow Check',
    format: 'compact',
    currency: true,
    income: [
      {label: 'Salary', value: 7000},
      {label: 'Side', value: 1000},
    ],
    expenses: [
      {label: 'Housing', value: 2200},
      {label: 'Living', value: 2500},
      {label: 'Debt', value: 900},
    ],
  },
  PORTFOLIO_ALLOCATION: {
    type: 'PORTFOLIO_ALLOCATION',
    title: 'Portfolio Allocation',
    format: 'compact',
    data: [
      {label: 'Global stocks', value: 60},
      {label: 'Bonds', value: 25},
      {label: 'Cash', value: 10},
      {label: 'Alternatives', value: 5},
    ],
    unit: '%',
  },
};

export const DataVizShowcase: React.FC = () => (
  <AbsoluteFill style={{background: DATAVIZ_THEME.background}}>
    {CHART_TYPES.map((type, index) => (
      <Sequence key={type} from={index * DATAVIZ_SEGMENT_FRAMES} durationInFrames={DATAVIZ_SEGMENT_FRAMES} name={type}>
        <AbsoluteFill>
          <div
            style={{
              position: 'absolute',
              top: 36,
              right: 48,
              zIndex: 2,
              fontSize: 18,
              letterSpacing: 2,
              color: DATAVIZ_THEME.accent,
              fontWeight: 800,
              textTransform: 'uppercase',
            }}
          >
            {type.replaceAll('_', ' ')}
          </div>
          <DataVizScene spec={demoSpecs[type]} />
        </AbsoluteFill>
      </Sequence>
    ))}
  </AbsoluteFill>
);

export const listDataVizDemoSpecs = (): ChartSpec[] => CHART_TYPES.map((type) => demoSpecs[type]);
