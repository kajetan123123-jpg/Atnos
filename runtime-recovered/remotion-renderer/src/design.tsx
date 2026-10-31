import React, {useEffect} from 'react';
import {MeasuredText} from './typography';
import {
  AbsoluteFill,
  continueRender,
  delayRender,
  interpolate,
  useCurrentFrame,
} from 'remotion';
import type {BrandSpec, CaptionStyleId, ManifestScene} from './contracts';
import {
  DEFAULT_CAPTION_STYLE,
  captionStateAtSceneTime,
} from './contracts';

export const SAFE_X = 72;
export const SAFE_TOP = 96;
export const SAFE_BOTTOM = 268;

export const FontLoader: React.FC<{
  regular?: string;
  extraBold?: string;
}> = ({regular, extraBold}) => {
  const [handle] = React.useState(() => delayRender('Loading deterministic Atnos fonts'));
  useEffect(() => {
    const sources = [
      regular ? new FontFace('Atnos', `url(${regular})`, {weight: '700'}) : null,
      extraBold ? new FontFace('Atnos', `url(${extraBold})`, {weight: '900'}) : null,
    ].filter((font): font is FontFace => font !== null);
    sources.forEach((font) => document.fonts.add(font));
    Promise.all(sources.map((font) => font.load()))
      .catch(() => undefined)
      .finally(() => continueRender(handle));
  }, [extraBold, handle, regular]);
  return null;
};

export const SceneShell: React.FC<{
  scene: ManifestScene;
  brand: BrandSpec;
  children: React.ReactNode;
  captionStyle?: CaptionStyleId;
  showBrandMark?: boolean;
  compactCaptions?: boolean;
}> = ({scene, brand, children, captionStyle, showBrandMark = false, compactCaptions = true}) => {
  const fromBrand = brand.captionStyle;
  const resolved: CaptionStyleId =
    captionStyle ??
    (fromBrand === 'plain_phrase' || fromBrand === 'paper_karaoke' || fromBrand === 'soft_pill' || fromBrand === 'outline_bold'
      ? fromBrand
      : DEFAULT_CAPTION_STYLE);
  return (
    <AbsoluteFill
      style={{
        background: brand.background,
        color: brand.text,
        fontFamily: 'Atnos, Montserrat, Arial Black, sans-serif',
        overflow: 'hidden',
      }}
    >
      {!brand.visualVariant && !brand.comparisonStyle ? <PaperField brand={brand} /> : null}
      {!brand.visualVariant && !brand.comparisonStyle && brand.presentation !== 'educational_illustration' ? <AmbientGrid brand={brand} /> : null}
      {showBrandMark ? <BrandMark brand={brand} /> : null}
      {children}
      <Caption scene={scene} brand={brand} styleId={resolved} compact={compactCaptions} />
    </AbsoluteFill>
  );
};

/** Warm paper wash so navy scenes read as blueprint sketchbook, not flat corporate void. */
const PaperField: React.FC<{brand: BrandSpec}> = ({brand}) => (
  <AbsoluteFill
    style={{
      background: `
        radial-gradient(ellipse 90% 55% at 50% 18%, ${brand.accent}22 0%, transparent 62%),
        radial-gradient(ellipse 70% 50% at 80% 88%, ${brand.accentSoft}33 0%, transparent 55%),
        linear-gradient(165deg, ${brand.background} 0%, ${brand.panel} 48%, ${brand.background} 100%)
      `,
    }}
  />
);

const AmbientGrid: React.FC<{brand: BrandSpec}> = ({brand}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill
      style={{
        backgroundImage: `linear-gradient(${brand.accentSoft}28 1px, transparent 1px), linear-gradient(90deg, ${brand.accentSoft}28 1px, transparent 1px)`,
        backgroundSize: '64px 64px',
        backgroundPosition: '0px 0px',
        maskImage: 'linear-gradient(to bottom, rgba(0,0,0,.72), rgba(0,0,0,.35) 55%, transparent 92%)',
        opacity: 0.95,
      }}
    />
  );
};

export const BrandMark: React.FC<{brand: BrandSpec}> = ({brand}) => (
  <div
    style={{
      position: 'absolute',
      zIndex: 20,
      left: SAFE_X,
      right: SAFE_X,
      top: SAFE_TOP,
      height: 72,
      display: 'flex',
      alignItems: 'center',
      gap: 16,
    }}
  >
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        padding: '10px 18px 10px 12px',
        borderRadius: 16,
        background: 'rgba(240,229,210,.94)',
        border: `2.5px solid ${brand.accent}`,
        boxShadow: '0 8px 22px rgba(0,0,0,.28)',
        color: '#142633',
        fontSize: 26,
        fontWeight: 900,
        letterSpacing: 3.6,
      }}
    >
      <svg width="48" height="48" viewBox="0 0 54 54" aria-hidden="true">
        <circle cx="27" cy="27" r="24" fill="#142633" stroke={brand.accent} strokeWidth="3.5" />
        <path d="M13 35L22 27L29 31L41 17" fill="none" stroke={brand.accent} strokeWidth="5.5" />
        <path d="M34 17H41V24" fill="none" stroke={brand.accent} strokeWidth="4.5" />
      </svg>
      {brand.wordmark}
    </div>
    <div
      style={{
        marginLeft: 'auto',
        padding: '10px 16px',
        borderRadius: 12,
        background: 'rgba(20,38,51,.82)',
        color: brand.accent,
        fontSize: 18,
        fontWeight: 900,
        letterSpacing: 3.2,
        border: `1.5px solid ${brand.accent}88`,
      }}
    >
      PERSONAL FINANCE
    </div>
  </div>
);

const displayWord = (raw: string): string => {
  // Sentence case for burned captions — never force ALL CAPS for full narration.
  const cleaned = raw.trim();
  if (!cleaned) return '';
  if (/^[A-Z0-9 .,'%-]+$/.test(cleaned) && cleaned.length <= 4) {
    return cleaned; // keep short acronyms / amounts as spoken
  }
  return cleaned;
};

const Caption: React.FC<{
  scene: ManifestScene;
  brand: BrandSpec;
  styleId: CaptionStyleId;
  compact?: boolean;
}> = ({scene, brand, styleId, compact = false}) => {
  const frame = useCurrentFrame();
  const state = captionStateAtSceneTime(scene, frame);
  if (!state.text) {
    return null;
  }
  const opacity = interpolate(frame, [0, 3], [0.7, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const words = state.cue?.words ?? state.text.split(/\s+/).filter(Boolean).map((text, index) => ({
    text,
    start_sec: 0,
    end_sec: 0,
    segment_id: `legacy-${index}`,
  }));

  const shell: React.CSSProperties = {
    position: 'absolute',
    zIndex: 30,
    left: SAFE_X - 8,
    right: SAFE_X - 8,
    bottom: SAFE_BOTTOM,
    display: 'flex',
    justifyContent: 'center',
    pointerEvents: 'none',
    opacity,
  };

  if (styleId === 'plain_phrase') {
    return <div data-caption-style="plain_phrase" style={{...shell, left: 72, right: 160, bottom: compact ? 230 : 310, height: compact ? 112 : 148, opacity: 1, alignItems: 'center'}}>
      <div style={{width: '100%', padding: compact ? '10px 22px' : '14px 22px', boxSizing: 'border-box', borderRadius: brand.visualVariant ? 0 : 12, background: compact ? 'rgba(16,30,40,.70)' : 'rgba(16,30,40,.86)', color: '#F0E5D2'}}>
        <MeasuredText text={state.text} width={804} maxSize={48} minSize={42} maxLines={2} weight={700} align="center" />
      </div>
    </div>;
  }

  if (styleId === 'soft_pill') {
    return (
      <div style={shell}>
        <div
          style={{
            maxWidth: 960,
            padding: '18px 28px 20px',
            borderRadius: 20,
            background: 'rgba(20,38,51,.88)',
            border: `2px solid ${brand.accent}`,
            boxShadow: '0 12px 32px rgba(10,20,30,.45)',
            textAlign: 'center',
            lineHeight: 1.2,
          }}
        >
          {words.map((word, index) => {
            const active = index === state.activeWordIndex;
            return (
              <span
                key={`${word.start_sec}-${index}`}
                style={{
                  display: 'inline',
                  margin: '0 7px',
                  fontSize: active ? 78 : 70,
                  fontWeight: 900,
                  color: active ? brand.accent : brand.text,
                }}
              >
                {displayWord(word.text)}
              </span>
            );
          })}
        </div>
      </div>
    );
  }

  if (styleId === 'outline_bold') {
    return (
      <div style={shell}>
        <div style={{maxWidth: 960, textAlign: 'center', lineHeight: 1.15}}>
          {words.map((word, index) => {
            const active = index === state.activeWordIndex;
            return (
              <span
                key={`${word.start_sec}-${index}`}
                style={{
                  display: 'inline',
                  margin: '0 6px',
                  fontSize: active ? 92 : 80,
                  fontWeight: 900,
                  color: active ? brand.accent : '#F0E5D2',
                  textShadow:
                    '0 0 3px #142633, 0 3px 0 #142633, 3px 0 0 #142633, -3px 0 0 #142633, 0 -3px 0 #142633',
                }}
              >
                {displayWord(word.text)}
              </span>
            );
          })}
        </div>
      </div>
    );
  }

  // paper_karaoke: phone-first plate. Ivory on a navy edge so it separates from cream paper and from navy scenes.
  const activeInk = '#142633';
  const ink = '#142633';
  return (
    <div data-caption-style="paper_karaoke" data-caption-min-font="76" style={shell}>
      <div
        style={{
          maxWidth: 980,
          minWidth: 760,
          padding: '26px 28px 28px',
          textAlign: 'center',
          lineHeight: 1.08,
          borderRadius: 22,
          background: '#FFF8EE',
          border: '6px solid #142633',
          boxShadow:
            '0 0 0 6px #C4A15A, 0 22px 40px rgba(8,16,24,.72)',
        }}
      >
        {words.map((word, index) => {
          const active = index === state.activeWordIndex;
          return (
            <span
              key={`${word.start_sec}-${index}`}
              style={{
                display: 'inline-block',
                margin: '4px 8px',
                padding: active ? '2px 12px 8px' : '2px 4px 8px',
                fontSize: active ? 84 : 76,
                fontWeight: 900,
                color: active ? activeInk : ink,
                background: active ? '#E2B15A' : 'transparent',
                borderRadius: 12,
                transform: active ? 'translateY(-2px)' : undefined,
              }}
            >
              {displayWord(word.text)}
            </span>
          );
        })}
      </div>
    </div>
  );
};
