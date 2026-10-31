import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {
  ANIMATION_PRESET_IDS,
  getAnimationPreset,
  listAnimationPresets,
  MotionLayer,
  sampleAnimation,
  type AnimationPresetId,
} from './motion';

export const SHOWCASE_FPS = 30;
export const SHOWCASE_WIDTH = 1080;
export const SHOWCASE_HEIGHT = 1920;
/** Frames allocated per preset (enter + hold). */
export const SHOWCASE_SEGMENT_FRAMES = 36;
export const SHOWCASE_COMPOSITION_ID = 'AnimationPresetShowcase';

export const showcaseDurationInFrames = (): number =>
  listAnimationPresets().length * SHOWCASE_SEGMENT_FRAMES;

const COLORS = {
  bg: '#111111',
  panel: '#1C1C1E',
  accent: '#39FF9F',
  accentSoft: '#0F5257',
  text: '#F5F5F5',
  muted: '#9CA8A2',
};

const PresetStage: React.FC<{presetId: AnimationPresetId}> = ({presetId}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const preset = getAnimationPreset(presetId);
  const sample = sampleAnimation(presetId, frame, fps);
  const countValue = Math.round(sample.progress * 100);

  return (
    <AbsoluteFill style={{background: COLORS.bg, color: COLORS.text, fontFamily: 'Arial, sans-serif'}}>
      <div
        style={{
          position: 'absolute',
          left: 64,
          right: 64,
          top: 120,
          padding: '18px 22px',
          borderRadius: 16,
          border: `1px solid ${COLORS.accentSoft}`,
          background: `${COLORS.panel}EE`,
        }}
      >
        <div style={{fontSize: 22, letterSpacing: 3, color: COLORS.accent, textTransform: 'uppercase'}}>
          Motion preset
        </div>
        <div style={{marginTop: 8, fontSize: 42, fontWeight: 800}}>{preset.id}</div>
        <div style={{marginTop: 6, fontSize: 24, color: COLORS.muted}}>
          {preset.label} · {preset.category}
        </div>
      </div>

      <div
        style={{
          position: 'absolute',
          left: 80,
          right: 80,
          top: 420,
          bottom: 280,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <MotionLayer preset={presetId} style={{width: '100%', display: 'flex', justifyContent: 'center'}}>
          <PresetVisual presetId={presetId} progress={sample.progress} countValue={countValue} />
        </MotionLayer>
      </div>

      <div
        style={{
          position: 'absolute',
          left: 64,
          right: 64,
          bottom: 140,
          fontSize: 20,
          color: COLORS.muted,
          textAlign: 'center',
        }}
      >
        duration {preset.defaults.duration}s · easing {preset.defaults.easing} · intensity{' '}
        {preset.defaults.intensity}
      </div>
    </AbsoluteFill>
  );
};

const PresetVisual: React.FC<{
  presetId: AnimationPresetId;
  progress: number;
  countValue: number;
}> = ({presetId, progress, countValue}) => {
  if (presetId.startsWith('TEXT_') || presetId === 'HIGHLIGHT' || presetId === 'MASK_REVEAL') {
    return (
      <div
        style={{
          fontSize: 64,
          fontWeight: 800,
          textAlign: 'center',
          maxWidth: 820,
          lineHeight: 1.15,
          color: COLORS.text,
        }}
      >
        Clear motion beats noise
      </div>
    );
  }
  if (presetId === 'NUMBER_COUNT') {
    return (
      <div style={{fontSize: 140, fontWeight: 900, color: COLORS.accent}}>
        {countValue}
        <span style={{fontSize: 56}}>%</span>
      </div>
    );
  }
  if (presetId === 'ICON_POP' || presetId === 'ICON_FLOAT') {
    return (
      <div
        style={{
          width: 160,
          height: 160,
          borderRadius: 36,
          background: COLORS.accentSoft,
          border: `3px solid ${COLORS.accent}`,
          display: 'grid',
          placeItems: 'center',
          fontSize: 64,
          fontWeight: 800,
          color: COLORS.accent,
        }}
      >
        $
      </div>
    );
  }
  if (presetId.startsWith('ASSET_') || presetId.startsWith('CAMERA_') || presetId === 'PARALLAX') {
    return (
      <div
        style={{
          width: 520,
          height: 360,
          borderRadius: 24,
          background: `linear-gradient(145deg, ${COLORS.accentSoft}, ${COLORS.panel})`,
          border: `1px solid ${COLORS.accent}55`,
          display: 'grid',
          placeItems: 'center',
          fontSize: 36,
          fontWeight: 700,
        }}
      >
        Asset card
      </div>
    );
  }
  if (presetId.startsWith('CHARACTER_')) {
    return (
      <div
        style={{
          width: 280,
          height: 420,
          borderRadius: 28,
          background: '#142633',
          border: `2px solid ${COLORS.accent}66`,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'flex-end',
          paddingBottom: 28,
          gap: 12,
        }}
      >
        <div
          style={{
            width: 96,
            height: 96,
            borderRadius: '50%',
            background: '#F0E5D2',
          }}
        />
        <div style={{width: 140, height: 180, borderRadius: 20, background: '#426579'}} />
        <div style={{color: COLORS.muted, fontSize: 22}}>Host slot</div>
      </div>
    );
  }
  if (presetId === 'LINE_DRAW') {
    const length = 420;
    return (
      <svg width={520} height={220} viewBox="0 0 520 220">
        <path
          d="M40 170 C 140 40, 260 200, 380 70 S 500 120, 500 120"
          fill="none"
          stroke={COLORS.accent}
          strokeWidth={6}
          strokeLinecap="round"
          strokeDasharray={length}
          strokeDashoffset={length * (1 - progress)}
        />
      </svg>
    );
  }
  if (presetId === 'BAR_GROW' || presetId === 'CHART_GROW') {
    const heights = [0.45, 0.7, 0.55, 0.92, 0.62];
    return (
      <div style={{display: 'flex', alignItems: 'flex-end', gap: 22, height: 280}}>
        {heights.map((h, index) => (
          <div
            key={index}
            style={{
              width: 56,
              height: 280 * h * Math.max(0.04, progress),
              borderRadius: 12,
              background: index === 3 ? COLORS.accent : COLORS.accentSoft,
            }}
          />
        ))}
      </div>
    );
  }
  if (presetId === 'SHAKE_LIGHT' || presetId === 'IMPACT') {
    return (
      <div
        style={{
          padding: '28px 40px',
          borderRadius: 20,
          background: COLORS.panel,
          border: `2px solid ${COLORS.accent}`,
          fontSize: 48,
          fontWeight: 800,
        }}
      >
        Impact
      </div>
    );
  }
  return <div style={{fontSize: 48}}>Preset</div>;
};

export const AnimationShowcase: React.FC = () => {
  const presets = listAnimationPresets();
  return (
    <AbsoluteFill style={{background: COLORS.bg}}>
      {presets.map((presetId, index) => (
        <Sequence
          key={presetId}
          from={index * SHOWCASE_SEGMENT_FRAMES}
          durationInFrames={SHOWCASE_SEGMENT_FRAMES}
          name={presetId}
        >
          <PresetStage presetId={presetId} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};

export const showcaseSegmentCount = (): number => ANIMATION_PRESET_IDS.length;
