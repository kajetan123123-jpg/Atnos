import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {ChartFrame} from './ChartFrame';
import {ChartView} from './charts';
import {DATAVIZ_THEME, themeFromBrand} from './theme';
import type {BrandSpec} from '../contracts';
import type {ChartSpec, DataVizTheme} from './types';

export const DataVizScene: React.FC<{
  spec: ChartSpec;
  brand?: Partial<BrandSpec>;
  theme?: DataVizTheme;
}> = ({spec, brand, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const resolvedTheme = theme ?? (brand ? themeFromBrand(brand) : DATAVIZ_THEME);

  return (
    <AbsoluteFill>
      <ChartFrame
        title={spec.title}
        subtitle={spec.subtitle}
        theme={resolvedTheme}
        frame={frame}
        fps={fps}
        timing={spec.timing}
      >
        <ChartView spec={spec} theme={resolvedTheme} frame={frame} fps={fps} />
      </ChartFrame>
    </AbsoluteFill>
  );
};
