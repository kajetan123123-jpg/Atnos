import React from 'react';
import {formatNumber, formatPercent} from './format';
import {seriesColor} from './theme';
import type {DataVizTheme} from './types';
import {labelOpacity, niceDomain, progressAt, resolveTiming} from './timing';
import type {
  AreaChartSpec,
  BarChartSpec,
  CashflowSpec,
  ChartSpec,
  ComparisonSpec,
  CompoundGrowthSpec,
  DonutChartSpec,
  LineChartSpec,
  MoneyCounterSpec,
  PercentageCounterSpec,
  PortfolioAllocationSpec,
  TimelineSpec,
} from './types';

type Theme = DataVizTheme;

const SVG_W = 900;
const SVG_H = 980;
const PAD = {top: 72, right: 36, bottom: 110, left: 92};

const plotBox = () => ({
  x: PAD.left,
  y: PAD.top,
  w: SVG_W - PAD.left - PAD.right,
  h: SVG_H - PAD.top - PAD.bottom,
});

export const BarChartView: React.FC<{spec: BarChartSpec; theme: Theme; frame: number; fps: number}> = ({
  spec,
  theme,
  frame,
  fps,
}) => {
  const timing = resolveTiming(spec.timing);
  const box = plotBox();
  const values = spec.data.map((d) => d.value);
  const {min, max} = niceDomain(values);
  const range = Math.max(1e-9, max - min);
  const n = Math.max(1, spec.data.length);
  const gap = 18;
  const barW = Math.max(28, (box.w - gap * (n - 1)) / n);
  const labels = labelOpacity(frame, fps, timing);

  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${SVG_W} ${SVG_H}`} preserveAspectRatio="xMidYMid meet">
      {[0, 0.25, 0.5, 0.75, 1].map((t) => {
        const y = box.y + box.h * (1 - t);
        const tick = min + range * t;
        return (
          <g key={t}>
            <line x1={box.x} x2={box.x + box.w} y1={y} y2={y} stroke={theme.accentSoft} strokeWidth={2} />
            <text x={box.x - 14} y={y + 8} textAnchor="end" fill={theme.muted} fontSize={22} fontWeight={700}>
              {formatNumber(tick, {mode: spec.format, currency: spec.currency, unit: spec.unit})}
            </text>
          </g>
        );
      })}
      {spec.data.map((datum, index) => {
        const grow = progressAt(frame, fps, {
          delaySec: timing.stagger * index,
          durationSec: timing.dataEnterDuration,
          preset: 'BAR_GROW',
        });
        const h = ((datum.value - min) / range) * box.h * grow;
        const x = box.x + index * (barW + gap);
        const y = box.y + box.h - h;
        return (
          <g key={`${datum.label}-${index}`}>
            <rect x={x} y={y} width={barW} height={Math.max(0, h)} rx={14} fill={seriesColor(theme, index, datum.color)} />
            <text
              x={x + barW / 2}
              y={Math.max(box.y + 28, y - 16)}
              textAnchor="middle"
              fill={theme.text}
              fontSize={26}
              fontWeight={900}
              opacity={labels}
            >
              {formatNumber(datum.value, {mode: spec.format, currency: spec.currency, unit: spec.unit})}
            </text>
            <foreignObject x={x - 4} y={box.y + box.h + 16} width={barW + 8} height={80}>
              <div
                style={{
                  color: theme.muted,
                  fontSize: 22,
                  fontWeight: 800,
                  textAlign: 'center',
                  lineHeight: 1.15,
                  overflowWrap: 'anywhere',
                  opacity: labels,
                }}
              >
                {datum.label}
              </div>
            </foreignObject>
          </g>
        );
      })}
    </svg>
  );
};

const LineOrArea: React.FC<{
  spec: LineChartSpec | AreaChartSpec;
  theme: Theme;
  frame: number;
  fps: number;
  area: boolean;
}> = ({spec, theme, frame, fps, area}) => {
  const timing = resolveTiming(spec.timing);
  const box = plotBox();
  const values = spec.data.map((d) => d.value);
  const {min, max} = niceDomain(values);
  const range = Math.max(1e-9, max - min);
  const draw = progressAt(frame, fps, {delaySec: 0, durationSec: timing.dataEnterDuration, preset: 'LINE_DRAW'});
  const labels = labelOpacity(frame, fps, timing);
  const pts = spec.data.map((datum, index) => {
    const x = box.x + (spec.data.length === 1 ? box.w / 2 : (index / (spec.data.length - 1)) * box.w);
    const y = box.y + box.h - ((datum.value - min) / range) * box.h;
    return {x, y, datum};
  });
  const line = pts.map((p) => `${p.x},${p.y}`).join(' ');
  const areaPath = `${line} ${box.x + box.w},${box.y + box.h} ${box.x},${box.y + box.h}`;

  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${SVG_W} ${SVG_H}`} preserveAspectRatio="xMidYMid meet">
      {[0, 0.5, 1].map((t) => {
        const y = box.y + box.h * (1 - t);
        return <line key={t} x1={box.x} x2={box.x + box.w} y1={y} y2={y} stroke={theme.accentSoft} strokeWidth={2} />;
      })}
      {area ? (
        <polygon points={areaPath} fill={`${theme.accent}44`} opacity={draw} />
      ) : null}
      <polyline
        points={line}
        fill="none"
        stroke={theme.accent}
        strokeWidth={14}
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1}
        strokeDasharray={1}
        strokeDashoffset={1 - draw}
      />
      {pts.map(({x, y, datum}, index) => (
        <g key={`${datum.label}-${index}`} opacity={draw}>
          <circle cx={x} cy={y} r={14} fill={theme.accent} stroke={theme.text} strokeWidth={5} />
          <text x={x} y={Math.max(36, y - 28)} textAnchor="middle" fill={theme.text} fontSize={24} fontWeight={900} opacity={labels}>
            {formatNumber(datum.value, {mode: spec.format, currency: spec.currency})}
          </text>
          <foreignObject x={x - 70} y={box.y + box.h + 18} width={140} height={70}>
            <div style={{color: theme.muted, fontSize: 20, fontWeight: 800, textAlign: 'center', opacity: labels, overflowWrap: 'anywhere'}}>
              {datum.label}
            </div>
          </foreignObject>
        </g>
      ))}
    </svg>
  );
};

export const LineChartView: React.FC<{spec: LineChartSpec; theme: Theme; frame: number; fps: number}> = (props) => (
  <LineOrArea {...props} area={false} />
);

export const AreaChartView: React.FC<{spec: AreaChartSpec; theme: Theme; frame: number; fps: number}> = (props) => (
  <LineOrArea {...props} area />
);

export const DonutChartView: React.FC<{
  spec: DonutChartSpec | PortfolioAllocationSpec;
  theme: Theme;
  frame: number;
  fps: number;
}> = ({spec, theme, frame, fps}) => {
  const timing = resolveTiming(spec.timing);
  const total = spec.data.reduce((sum, d) => sum + Math.max(0, d.value), 0) || 1;
  const cx = 450;
  const cy = 420;
  const r = 260;
  const inner = 150;
  const draw = progressAt(frame, fps, {delaySec: 0, durationSec: timing.dataEnterDuration, preset: 'CHART_GROW'});
  const labels = labelOpacity(frame, fps, timing);
  let angle = -Math.PI / 2;

  const arcs = spec.data.map((datum, index) => {
    const portion = Math.max(0, datum.value) / total;
    const sweep = portion * Math.PI * 2 * draw;
    const start = angle;
    const end = angle + sweep;
    angle += portion * Math.PI * 2;
    const large = sweep > Math.PI ? 1 : 0;
    const x1 = cx + r * Math.cos(start);
    const y1 = cy + r * Math.sin(start);
    const x2 = cx + r * Math.cos(end);
    const y2 = cy + r * Math.sin(end);
    const ix1 = cx + inner * Math.cos(end);
    const iy1 = cy + inner * Math.sin(end);
    const ix2 = cx + inner * Math.cos(start);
    const iy2 = cy + inner * Math.sin(start);
    const d = [
      `M ${x1} ${y1}`,
      `A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`,
      `L ${ix1} ${iy1}`,
      `A ${inner} ${inner} 0 ${large} 0 ${ix2} ${iy2}`,
      'Z',
    ].join(' ');
    return {d, datum, index, portion};
  });

  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${SVG_W} ${SVG_H}`} preserveAspectRatio="xMidYMid meet">
      {arcs.map((arc) => (
        <path key={arc.index} d={arc.d} fill={seriesColor(theme, arc.index, arc.datum.color)} />
      ))}
      <text x={cx} y={cy - 8} textAnchor="middle" fill={theme.text} fontSize={42} fontWeight={900}>
        {formatPercent((spec.data[0]?.value ?? 0) / total * 100, {mode: spec.format})}
      </text>
      <text x={cx} y={cy + 36} textAnchor="middle" fill={theme.muted} fontSize={22} fontWeight={700}>
        top slice
      </text>
      {spec.data.map((datum, index) => (
        <foreignObject key={datum.label} x={60} y={720 + index * 52} width={780} height={48}>
          <div style={{display: 'flex', alignItems: 'center', gap: 14, opacity: labels}}>
            <span style={{width: 18, height: 18, borderRadius: 4, background: seriesColor(theme, index, datum.color)}} />
            <span style={{flex: 1, color: theme.text, fontSize: 24, fontWeight: 700, overflowWrap: 'anywhere'}}>{datum.label}</span>
            <span style={{color: theme.accent, fontSize: 24, fontWeight: 900}}>
              {formatNumber(datum.value, {mode: spec.format, currency: spec.currency, unit: spec.unit})}
            </span>
          </div>
        </foreignObject>
      ))}
    </svg>
  );
};

export const ComparisonView: React.FC<{spec: ComparisonSpec; theme: Theme; frame: number; fps: number}> = ({
  spec,
  theme,
  frame,
  fps,
}) => {
  const timing = resolveTiming(spec.timing);
  const grow = progressAt(frame, fps, {delaySec: 0, durationSec: timing.dataEnterDuration, preset: 'BAR_GROW'});
  const labels = labelOpacity(frame, fps, timing);
  const max = Math.max(spec.left.value, spec.right.value, 1);
  const tone = (side: typeof spec.left) =>
    side.tone === 'negative' ? theme.danger : side.tone === 'positive' ? theme.accent : theme.warn;

  const panel = (side: typeof spec.left, align: 'left' | 'right') => {
    const h = 520 * (side.value / max) * grow;
    return (
      <div style={{flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0}}>
        <div style={{fontSize: 28, fontWeight: 900, color: tone(side), opacity: labels, overflowWrap: 'anywhere'}}>{side.label}</div>
        <div style={{marginTop: 18, fontSize: 48, fontWeight: 900, opacity: labels}}>
          {formatNumber(side.value, {mode: spec.format, currency: spec.currency})}
        </div>
        <div style={{marginTop: 'auto', height: 560, display: 'flex', alignItems: 'flex-end'}}>
          <div style={{width: '100%', height: h, borderRadius: 22, background: tone(side)}} />
        </div>
      </div>
    );
  };

  return (
    <div style={{height: '100%', display: 'flex', gap: 36, padding: '12px 8px'}}>
      {panel(spec.left, 'left')}
      <div style={{width: 4, background: theme.accentSoft, borderRadius: 4}} />
      {panel(spec.right, 'right')}
    </div>
  );
};

export const TimelineView: React.FC<{spec: TimelineSpec; theme: Theme; frame: number; fps: number}> = ({
  spec,
  theme,
  frame,
  fps,
}) => {
  const timing = resolveTiming(spec.timing);
  const draw = progressAt(frame, fps, {delaySec: 0, durationSec: timing.dataEnterDuration, preset: 'LINE_DRAW'});
  const labels = labelOpacity(frame, fps, timing);
  const n = Math.max(1, spec.events.length);

  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${SVG_W} ${SVG_H}`} preserveAspectRatio="xMidYMid meet">
      <line x1={80} x2={80 + 740 * draw} y1={420} y2={420} stroke={theme.accent} strokeWidth={10} strokeLinecap="round" />
      {spec.events.map((event, index) => {
        const x = 80 + (n === 1 ? 370 : (index / (n - 1)) * 740);
        const appear = progressAt(frame, fps, {
          delaySec: timing.stagger * index,
          durationSec: 0.4,
          preset: 'FADE_IN',
        });
        return (
          <g key={`${event.at}-${index}`} opacity={appear}>
            <circle cx={x} cy={420} r={18} fill={theme.accent} stroke={theme.text} strokeWidth={5} />
            <foreignObject x={x - 110} y={160} width={220} height={220}>
              <div style={{textAlign: 'center', opacity: labels}}>
                <div style={{color: theme.accent, fontSize: 20, fontWeight: 800}}>{event.at}</div>
                <div style={{marginTop: 10, color: theme.text, fontSize: 26, fontWeight: 900, overflowWrap: 'anywhere'}}>
                  {event.label}
                </div>
                {event.value != null ? (
                  <div style={{marginTop: 8, color: theme.muted, fontSize: 22, fontWeight: 700}}>
                    {formatNumber(event.value, {mode: spec.format, currency: spec.currency})}
                  </div>
                ) : null}
              </div>
            </foreignObject>
          </g>
        );
      })}
    </svg>
  );
};

export const MoneyCounterView: React.FC<{spec: MoneyCounterSpec; theme: Theme; frame: number; fps: number}> = ({
  spec,
  theme,
  frame,
  fps,
}) => {
  const timing = resolveTiming(spec.timing);
  const progress = progressAt(frame, fps, {delaySec: 0, durationSec: timing.dataEnterDuration, preset: 'NUMBER_COUNT'});
  const value = spec.value * progress;
  const labels = labelOpacity(frame, fps, timing);
  return (
    <div style={{height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
      <div style={{fontSize: 28, letterSpacing: 3, color: theme.muted, fontWeight: 800, opacity: labels, textTransform: 'uppercase'}}>
        {spec.subtitle ?? 'Balance'}
      </div>
      <div style={{marginTop: 28, fontSize: 120, fontWeight: 900, color: theme.accent, lineHeight: 1, textAlign: 'center'}}>
        {spec.prefix ?? ''}
        {formatNumber(value, {mode: spec.format, currency: spec.currency ?? true, forceFull: progress < 0.98 && Math.abs(spec.value) < 10000})}
      </div>
      <div style={{marginTop: 24, fontSize: 26, color: theme.muted, opacity: labels}}>
        Full: {formatNumber(spec.value, {mode: 'full', currency: spec.currency ?? true})}
      </div>
    </div>
  );
};

export const PercentageCounterView: React.FC<{
  spec: PercentageCounterSpec;
  theme: Theme;
  frame: number;
  fps: number;
}> = ({spec, theme, frame, fps}) => {
  const timing = resolveTiming(spec.timing);
  const progress = progressAt(frame, fps, {delaySec: 0, durationSec: timing.dataEnterDuration, preset: 'NUMBER_COUNT'});
  const value = spec.value * progress;
  return (
    <div style={{height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
      <div style={{fontSize: 160, fontWeight: 900, color: theme.accent}}>{formatPercent(value, {mode: spec.format})}</div>
    </div>
  );
};

export const CompoundGrowthView: React.FC<{
  spec: CompoundGrowthSpec;
  theme: Theme;
  frame: number;
  fps: number;
}> = ({spec, theme, frame, fps}) => <LineChartView spec={{...spec, type: 'LINE_CHART'}} theme={theme} frame={frame} fps={fps} />;

export const CashflowView: React.FC<{spec: CashflowSpec; theme: Theme; frame: number; fps: number}> = ({
  spec,
  theme,
  frame,
  fps,
}) => {
  const timing = resolveTiming(spec.timing);
  const labels = labelOpacity(frame, fps, timing);
  const rows = Math.max(spec.income.length, spec.expenses.length);
  const incomeTotal = spec.income.reduce((s, d) => s + d.value, 0);
  const expenseTotal = spec.expenses.reduce((s, d) => s + d.value, 0);
  const max = Math.max(incomeTotal, expenseTotal, 1);
  const incomeGrow = progressAt(frame, fps, {delaySec: 0, durationSec: timing.dataEnterDuration, preset: 'BAR_GROW'});
  const expenseGrow = progressAt(frame, fps, {
    delaySec: timing.stagger,
    durationSec: timing.dataEnterDuration,
    preset: 'BAR_GROW',
  });

  return (
    <div style={{height: '100%', display: 'flex', flexDirection: 'column', gap: 36, justifyContent: 'center', padding: 12}}>
      {[
        {label: 'Income', value: incomeTotal, color: theme.accent, grow: incomeGrow},
        {label: 'Expenses', value: expenseTotal, color: theme.danger, grow: expenseGrow},
      ].map((row) => (
        <div key={row.label}>
          <div style={{display: 'flex', justifyContent: 'space-between', marginBottom: 12, opacity: labels}}>
            <span style={{fontSize: 28, fontWeight: 800}}>{row.label}</span>
            <span style={{fontSize: 28, fontWeight: 900, color: row.color}}>
              {formatNumber(row.value, {mode: spec.format, currency: spec.currency ?? true})}
            </span>
          </div>
          <div style={{height: 54, borderRadius: 16, background: theme.accentSoft, overflow: 'hidden'}}>
            <div
              style={{
                width: `${(row.value / max) * 100 * row.grow}%`,
                height: '100%',
                background: row.color,
                borderRadius: 16,
              }}
            />
          </div>
        </div>
      ))}
      <div style={{marginTop: 12, fontSize: 26, color: theme.muted, opacity: labels}}>
        Net {formatNumber(incomeTotal - expenseTotal, {mode: spec.format, currency: spec.currency ?? true})} · {rows} categories
      </div>
    </div>
  );
};

export const ChartView: React.FC<{
  spec: ChartSpec;
  theme: Theme;
  frame: number;
  fps: number;
}> = ({spec, theme, frame, fps}) => {
  switch (spec.type) {
    case 'BAR_CHART':
      return <BarChartView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'LINE_CHART':
      return <LineChartView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'AREA_CHART':
      return <AreaChartView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'DONUT_CHART':
      return <DonutChartView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'PORTFOLIO_ALLOCATION':
      return <DonutChartView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'COMPARISON':
      return <ComparisonView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'TIMELINE':
      return <TimelineView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'MONEY_COUNTER':
      return <MoneyCounterView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'PERCENTAGE_COUNTER':
      return <PercentageCounterView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'COMPOUND_GROWTH':
      return <CompoundGrowthView spec={spec} theme={theme} frame={frame} fps={fps} />;
    case 'CASHFLOW':
      return <CashflowView spec={spec} theme={theme} frame={frame} fps={fps} />;
    default: {
      const _exhaustive: never = spec;
      return _exhaustive;
    }
  }
};
