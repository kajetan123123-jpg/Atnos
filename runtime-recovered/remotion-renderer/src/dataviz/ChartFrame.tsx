import React from 'react';
import type {DataVizTheme} from './types';
import {labelOpacity, resolveTiming} from './timing';
import type {ChartTiming} from './types';

export const ChartFrame: React.FC<{
  title: string;
  subtitle?: string;
  theme: DataVizTheme;
  frame: number;
  fps: number;
  timing?: ChartTiming;
  children: React.ReactNode;
  footer?: React.ReactNode;
}> = ({title, subtitle, theme, frame, fps, timing, children, footer}) => {
  const resolved = resolveTiming(timing);
  const titleOpacity = labelOpacity(frame, fps, {...resolved, labelDelay: 0.05, labelDuration: 0.35});
  const bodyOpacity = Math.min(1, Math.max(0, (frame - 4) / 12));

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        background: theme.background,
        color: theme.text,
        fontFamily: 'Arial, Helvetica, sans-serif',
        boxSizing: 'border-box',
        padding: '96px 64px 120px',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div style={{opacity: titleOpacity, marginBottom: 28}}>
        <div
          style={{
            fontSize: 18,
            letterSpacing: 4,
            textTransform: 'uppercase',
            color: theme.accent,
            fontWeight: 700,
          }}
        >
          {theme.wordmark}
        </div>
        <div
          style={{
            marginTop: 18,
            fontSize: 52,
            lineHeight: 1.1,
            fontWeight: 900,
            overflowWrap: 'anywhere',
            wordBreak: 'break-word',
          }}
        >
          {title}
        </div>
        {subtitle ? (
          <div style={{marginTop: 12, fontSize: 26, color: theme.muted, lineHeight: 1.3}}>{subtitle}</div>
        ) : null}
      </div>

      <div
        style={{
          flex: 1,
          opacity: bodyOpacity,
          borderRadius: 28,
          background: theme.panel,
          border: `2px solid ${theme.accentSoft}`,
          padding: '36px 28px 40px',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          overflow: 'visible',
        }}
      >
        <div style={{flex: 1, minHeight: 0, overflow: 'visible'}}>{children}</div>
        {footer ? <div style={{marginTop: 20, overflow: 'visible'}}>{footer}</div> : null}
      </div>
    </div>
  );
};
