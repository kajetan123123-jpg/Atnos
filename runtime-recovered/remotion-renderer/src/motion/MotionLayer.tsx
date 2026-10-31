import React, {type CSSProperties, type ReactNode} from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {
  getAnimationPreset,
  maskRevealClip,
  motionToCss,
  sampleAnimation,
} from './registry';
import type {AnimationParams, AnimationPresetId} from './types';

type MotionLayerProps = {
  preset: AnimationPresetId;
  params?: Partial<AnimationParams>;
  children: ReactNode;
  style?: CSSProperties;
  className?: string;
};

/**
 * Applies a registry preset to children. Layouts share this — do not copy keyframes.
 */
export const MotionLayer: React.FC<MotionLayerProps> = ({
  preset,
  params,
  children,
  style,
  className,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sample = sampleAnimation(preset, frame, fps, params);
  const defaults = getAnimationPreset(preset).defaults;
  const direction = params?.direction ?? defaults.direction;
  const motionStyle = motionToCss(sample);
  if (preset === 'MASK_REVEAL' || preset === 'TEXT_REVEAL') {
    motionStyle.clipPath = maskRevealClip(sample, direction);
  }
  return (
    <div className={className} style={{...motionStyle, ...style}}>
      {children}
    </div>
  );
};
