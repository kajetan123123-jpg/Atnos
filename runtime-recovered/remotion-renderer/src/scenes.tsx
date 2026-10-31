import React from 'react';
import {AbsoluteFill, Img, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {BrandSpec, ManifestScene, SemanticOverlayKind} from './contracts';
import {chartValues, headlineFor, imageHeadlineFor, numericToken} from './contracts';
import {SAFE_X, SceneShell} from './design';
import {MeasuredText} from './typography';
import {MotionLayer} from './motion';
import type {AnimationPresetId} from './motion/types';
import {VerifiedCompoundChart} from './VerifiedCompoundChart';
import {ReserveReadinessScene} from './ReserveReadinessScene';

type SceneProps = {scene: ManifestScene; brand: BrandSpec; imageSrc?: string | null};

const entryTransform = (frame: number) => ({
  opacity: interpolate(frame, [0, 8], [0, 1], {extrapolateRight: 'clamp'}),
  transform: `translateY(${interpolate(frame, [0, 12], [44, 0], {extrapolateRight: 'clamp'})}px)`,
});

export const chartValueLabel = (value: number, currency: boolean): string => {
  const formatted = Number.isInteger(value)
    ? value.toFixed(0)
    : value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return `${currency ? '$' : ''}${formatted}`;
};

export const hookNumbersFor = (scene: ManifestScene): [string, string] | null => {
  const source = `${scene.headline ?? ''} ${scene.overlay_text ?? ''} ${scene.narration} ${scene.caption}`;
  const matches = source.match(/(?:[$€£])-?\d+(?:[.,]\d+)?/g) ?? [];
  const unique = [...new Set(matches.map((value) => value.replace(',', '.')))];
  return unique.length >= 2 ? [unique[0], unique[1]] : null;
};

export const hookCardCopyFor = (scene: ManifestScene): string[] =>
  hookNumbersFor(scene) ?? [scene.headline?.trim() || scene.overlay_text?.trim() || scene.caption];

export const hookMotionFor = (frame: number, durationInFrames: number) => {
  const end = Math.max(1, durationInFrames - 1);
  return {
    progress: interpolate(frame, [0, end], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    }),
    destinationOpacity: interpolate(frame, [0, end * 0.42, end * 0.72], [0.32, 0.32, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    }),
    destinationScale: interpolate(frame, [0, end * 0.58, end], [0.94, 0.94, 1.04], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    }),
  };
};

export const imageSceneTreatment = (usesCompoundDiagram: boolean, hasSemanticOverlay: boolean) => ({
  filter: usesCompoundDiagram
    ? 'brightness(1.18) contrast(1.03) saturate(.68)'
    : 'brightness(1.30) contrast(1.01)',
  opacity: usesCompoundDiagram ? 0.9 : 1,
  overlay: usesCompoundDiagram
    ? 'linear-gradient(to bottom, rgba(7,12,10,.36) 0%, rgba(7,12,10,.04) 34%, rgba(7,12,10,.04) 62%, rgba(7,12,10,.56) 88%, rgba(7,12,10,.72) 100%)'
    : hasSemanticOverlay
      ? 'linear-gradient(to bottom, rgba(7,12,10,.58) 0%, rgba(7,12,10,.05) 24%, rgba(7,12,10,.08) 58%, rgba(7,12,10,.76) 88%, rgba(7,12,10,.88) 100%)'
      : 'linear-gradient(to bottom, transparent 46%, rgba(17,17,17,.92) 100%)',
});

/** Icon meaning diagrams replace the decorative still; split_screen must keep the host underlay. */
export const meaningDiagramReplacesUnderlay = (layoutId: string, hasSemanticOverlay: boolean): boolean =>
  Boolean(hasSemanticOverlay) && layoutId.trim() === 'icon_explainer';

/** A production background is a complete composition, not an icon or a host cutout. */
export const usesBackgroundIllustrationFrame = (scene: ManifestScene): boolean => {
  const selected = scene.assets.find((asset) => asset.role === 'selected_image');
  const parameters = selected?.provenance?.generation_parameters;
  return !scene.semantic_overlay_kind && parameters?.production_role === 'background' && parameters.fit === 'cover';
};

const BackgroundIllustrationScene: React.FC<SceneProps & {imageSrc: string}> = ({scene, brand, imageSrc}) => {
  const frame = useCurrentFrame();
  // Reveal once to introduce the new situation, then hold its framing for comprehension.
  const opacity = interpolate(frame, [0, 10], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <SceneShell scene={scene} brand={brand}>
      <AbsoluteFill style={{opacity}}>
        <Img src={imageSrc} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: '50% 45%'}} />
        <AbsoluteFill style={{background: 'linear-gradient(to bottom, rgba(16,26,36,.55) 0%, transparent 27%, transparent 57%, rgba(16,26,36,.88) 83%, rgba(16,26,36,.96) 100%)'}} />
      </AbsoluteFill>
      <div style={{position: 'absolute', zIndex: 14, left: SAFE_X, right: SAFE_X, top: 1160, color: brand.text, opacity}}>
        <MeasuredText text={imageHeadlineFor(scene)} width={936} maxSize={72} minSize={48} maxLines={3} />
      </div>
    </SceneShell>
  );
};

export const HookScene: React.FC<SceneProps> = ({scene, brand}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  if (scene.semantic_overlay_kind === 'reserve_readiness') return <ReserveReadinessScene scene={scene} brand={brand} />;
  const motion = hookMotionFor(frame, durationInFrames);
  const hasTimedCaptions = Boolean(scene.caption_words && scene.caption_words.length > 0);
  const cardCopy = hookCardCopyFor(scene);
  const numbers = !hasTimedCaptions && cardCopy.length === 2 ? ([cardCopy[0], cardCopy[1]] as [string, string]) : null;
  const raiseFlow = scene.semantic_overlay_kind === 'raise_absorption';
  const title = headlineFor(scene, 7) || (raiseFlow ? 'WHERE THE RAISE GOES' : 'THE RAISE GAP');
  return (
    <SceneShell scene={scene} brand={brand}>
      <div style={{position: 'absolute', top: 245, left: SAFE_X, right: SAFE_X, ...entryTransform(frame)}}>
        <div style={{color: brand.accent, fontSize: 28, fontWeight: 900, letterSpacing: 6}}>MONEY, EXPLAINED</div>
        <div style={{marginTop: 28, color: brand.text}}>
          <MeasuredText text={title} width={936} maxSize={hasTimedCaptions ? 72 : 88} minSize={36} maxLines={3} />
        </div>
        <div style={{marginTop: 36, width: 168 + motion.progress * 300, height: 12, background: brand.accent, borderRadius: 6}} />
      </div>
      {raiseFlow ? <div style={{position: 'absolute', top: 720, left: SAFE_X, right: SAFE_X, height: 460}}>
        {/* Blueprint card behind the arc — distinctive sketchbook panel */}
        <div
          style={{
            position: 'absolute',
            inset: '20px 0 0',
            borderRadius: 28,
            background: `linear-gradient(180deg, ${brand.panel}EE 0%, ${brand.background}CC 100%)`,
            border: `3px solid ${brand.accent}99`,
            boxShadow: '0 18px 48px rgba(0,0,0,.35)',
          }}
        />
        {raiseFlow ? (
          <svg width="936" height="410" viewBox="0 0 936 410" aria-hidden="true" style={{position: 'absolute', inset: '40px 0 0', zIndex: 2}}>
            {[
              {x: 40, label: 'OLD PAY', sub: 'BEFORE'},
              {x: 330, label: 'THE RAISE', sub: 'NEW'},
              {x: 620, label: 'KEEP', sub: 'NOT ALL SPENT'},
            ].map((box, index) => (
              <g key={box.label} opacity={0.35 + motion.progress * (0.65 - index * 0.08)}>
                <rect x={box.x} y="90" width="250" height="180" rx="28" fill={brand.panel} stroke={brand.accent} strokeWidth="5" />
                <text x={box.x + 125} y="160" textAnchor="middle" fill={brand.text} fontSize="28" fontWeight="900" letterSpacing="2">
                  {box.label}
                </text>
                <text x={box.x + 125} y="210" textAnchor="middle" fill={brand.accent} fontSize="24" fontWeight="800" letterSpacing="2">
                  {box.sub}
                </text>
                {index < 2 ? (
                  <polygon
                    points={`${box.x + 260},165 ${box.x + 290},180 ${box.x + 260},195`}
                    fill={brand.accent}
                  />
                ) : null}
              </g>
            ))}
          </svg>
        ) : null}
        {numbers ? (
          <>
            <div
              style={{
                position: 'absolute', left: 0, top: 218, width: 330, padding: '32px 22px', borderRadius: 26,
                border: `3px solid ${brand.accentSoft}`, background: 'rgba(7,18,14,.90)', textAlign: 'center', zIndex: 3,
              }}
            >
              <div style={{fontSize: 88, fontWeight: 900}}>{numbers[0]}</div>
            </div>
            <div
              style={{
                position: 'absolute', right: 0, top: 218, width: 390, padding: '32px 22px', borderRadius: 26,
                border: `3px solid ${brand.accent}`, background: 'rgba(7,18,14,.94)', textAlign: 'center',
                opacity: motion.destinationOpacity, transform: `scale(${motion.destinationScale})`, zIndex: 3,
              }}
            >
              <div style={{fontSize: 82, fontWeight: 900}}>{numbers[1]}</div>
            </div>
          </>
        ) : hasTimedCaptions ? null : (
          <div
            style={{
              position: 'absolute', left: 58, right: 58, top: 218, padding: '34px 42px', borderRadius: 26,
              border: `3px solid ${brand.accent}`, background: 'rgba(7,18,14,.94)', textAlign: 'center',
              opacity: motion.destinationOpacity, zIndex: 3,
            }}
          >
            <div style={{fontSize: 42, lineHeight: 1.14, fontWeight: 900}}>{cardCopy[0]}</div>
          </div>
        )}
      </div> : null}
    </SceneShell>
  );
};

export const ImageScene: React.FC<SceneProps> = ({scene, brand, imageSrc}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  if (!imageSrc) {
    throw new Error(`ImageScene ${scene.scene_id} has no selected or candidate image data`);
  }
  if (usesBackgroundIllustrationFrame(scene)) {
    return <BackgroundIllustrationScene scene={scene} brand={brand} imageSrc={imageSrc} />;
  }
  const layoutId = (scene.layout_id || '').trim();
  if (
    layoutId === 'character_left_text_right' ||
    layoutId === 'character_right_text_left' ||
    layoutId === 'split_screen' ||
    layoutId === 'icon_explainer'
  ) {
    return <LaidOutImageScene scene={scene} brand={brand} imageSrc={imageSrc} layoutId={layoutId} />;
  }
  const zoom = interpolate(frame, [0, Math.max(1, durationInFrames - 1)], [1.03, 1.14]);
  const semanticTheme = scene.semantic_overlay_kind ? semanticThemeFor(scene.semantic_overlay_kind) : null;
  const usesCompoundDiagram = scene.semantic_overlay_kind?.startsWith('compound_') ?? false;
  const composition = scene.semantic_overlay_kind
    ? semanticCompositionFor(scene.semantic_overlay_kind)
    : DEFAULT_SEMANTIC_COMPOSITION;
  const treatment = imageSceneTreatment(usesCompoundDiagram, Boolean(scene.semantic_overlay_kind));
  const x = interpolate(
    frame,
    [0, Math.max(1, durationInFrames - 1)],
    [composition.panFrom, composition.panTo],
  );
  return (
    <SceneShell scene={scene} brand={brand}>
      {semanticTheme ? (
        <AbsoluteFill
          style={{
            background: `radial-gradient(circle at ${semanticTheme.glowPosition}, ${semanticTheme.glow} 0%, transparent 58%)`,
            opacity: 0.92,
          }}
        />
      ) : null}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          overflow: 'hidden',
        }}
      >
        <Img
          src={imageSrc}
          style={{
            width: '100%',
            height: '100%',
            objectFit:
              scene.assets.find((asset) => asset.role === 'selected_image')?.provenance
                ?.generation_parameters?.fit === 'cover'
                ? 'cover'
                : 'contain',
            objectPosition: 'center bottom',
            filter: treatment.filter,
            opacity: treatment.opacity,
            transform: `translateX(${x}px) scale(${zoom})`,
          }}
        />
        <AbsoluteFill
          style={{
            background: treatment.overlay,
          }}
        />
        {scene.semantic_overlay_kind && composition.overlayTreatment !== 'none' ? (
          <FinanceSemanticOverlay
            kind={scene.semantic_overlay_kind}
            brand={brand}
            theme={semanticTheme!}
            composition={composition}
            relation={scene.visual_relation}
          />
        ) : null}
      </div>
      <div
        style={{
          position: 'absolute',
          zIndex: 14,
          left: composition.headlineLeft,
          right: composition.headlineRight,
          top: composition.headlineTop,
          fontSize: composition.headlineSize,
          fontWeight: 900,
          lineHeight: 0.98,
          textAlign: composition.headlineAlign,
          textShadow: '0 4px 22px rgba(0,0,0,.55)',
          // Timed captions carry spoken text — do not stack a second narration headline.
          display: scene.caption_words && scene.caption_words.length > 0 ? 'none' : undefined,
        }}
      >
        {headlineFor(scene, 5)}
      </div>
    </SceneShell>
  );
};

type LaidOutImageSceneProps = SceneProps & {layoutId: string; imageSrc: string};

const LaidOutImageScene: React.FC<LaidOutImageSceneProps> = ({scene, brand, imageSrc, layoutId}) => {
  const presets = scene.animation_presets ?? [];
  const characterPreset =
    (presets.find((id) => id.startsWith('CHARACTER_')) as AnimationPresetId | undefined) ??
    (layoutId === 'character_left_text_right' ? 'CHARACTER_ENTER_LEFT' : 'CHARACTER_ENTER_RIGHT');
  const textPreset =
    (presets.find((id) => id.startsWith('TEXT_') || id.startsWith('ICON_')) as AnimationPresetId | undefined) ??
    (layoutId === 'icon_explainer' ? 'ICON_POP' : 'TEXT_FADE');

  const characterLeft = layoutId === 'character_left_text_right';
  const iconMode = layoutId === 'icon_explainer';
  const splitMode = layoutId === 'split_screen';
  const semanticTheme = scene.semantic_overlay_kind ? semanticThemeFor(scene.semantic_overlay_kind) : null;
  const composition = scene.semantic_overlay_kind
    ? semanticCompositionFor(scene.semantic_overlay_kind)
    : DEFAULT_SEMANTIC_COMPOSITION;
  // Icon explainers: diagram replaces decorative stills.
  // Split screen: keep Alex cutout AND a side diagram (host points at the mechanism).
  const preferMeaningDiagram = meaningDiagramReplacesUnderlay(layoutId, Boolean(scene.semantic_overlay_kind));
  const hostWithDiagram = Boolean(scene.semantic_overlay_kind) && splitMode && Boolean(semanticTheme);
  const paperScene = preferMeaningDiagram || hostWithDiagram;

  const imageBox = preferMeaningDiagram
    ? {left: '5%', top: '14%', width: '90%', height: '58%', objectFit: 'contain' as const}
    : hostWithDiagram
    ? {left: '2%', top: '18%', width: '38%', height: '58%', objectFit: 'contain' as const}
    : iconMode
    ? {left: SAFE_X, top: 260, width: 936, height: 850, objectFit: 'contain' as const}
    : characterLeft
      ? {left: '2%', top: '16%', width: '46%', height: '62%', objectFit: 'contain' as const}
      : {left: '52%', top: '16%', width: '46%', height: '62%', objectFit: 'contain' as const};

  const textBox = iconMode || preferMeaningDiagram || hostWithDiagram
    ? {left: SAFE_X, right: SAFE_X, top: 1150, textAlign: 'center' as const}
    : characterLeft
      ? {left: 540, right: SAFE_X, top: 420, textAlign: 'left' as const}
      : {left: SAFE_X, right: 540, top: 420, textAlign: 'left' as const};

  // Meaning diagrams already carry the visual idea; captions carry VO — skip a third chrome headline box.
  const showBoxedHeadline = !preferMeaningDiagram && !hostWithDiagram;

  return (
    <SceneShell scene={scene} brand={brand}>
      {paperScene ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: `radial-gradient(ellipse at 50% 28%, #F7F0E4 0%, #F0E5D2 55%, #E4D6BC 100%)`,
            opacity: 0.97,
          }}
        />
      ) : null}
      {paperScene ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            backgroundImage:
              'linear-gradient(rgba(66,101,121,.14) 1px, transparent 1px), linear-gradient(90deg, rgba(66,101,121,.14) 1px, transparent 1px)',
            backgroundSize: '72px 72px',
            opacity: 0.55,
            pointerEvents: 'none',
          }}
        />
      ) : null}
      {!paperScene ? (
        <div
          style={{
            position: 'absolute',
            left: 48,
            right: 48,
            top: 190,
            bottom: 280,
            borderRadius: 28,
            border: `1.5px solid ${brand.accentSoft}66`,
            background: `linear-gradient(165deg, ${brand.panel}CC 0%, ${brand.background} 70%)`,
            boxShadow: `inset 0 0 0 1px ${brand.accent}22`,
          }}
        />
      ) : null}
      {preferMeaningDiagram && scene.semantic_overlay_kind && semanticTheme ? (
        <FinanceSemanticOverlay
          kind={scene.semantic_overlay_kind}
          brand={brand}
          theme={semanticTheme}
          composition={composition}
          relation={scene.visual_relation}
        />
      ) : null}
      {hostWithDiagram && scene.semantic_overlay_kind && semanticTheme ? (
        <FinanceSemanticOverlay
          kind={scene.semantic_overlay_kind}
          brand={brand}
          theme={semanticTheme}
          composition={{...composition, overlayLeft: 420, overlayTop: 210, overlayScale: 0.58}}
          relation={scene.visual_relation}
        />
      ) : null}
      {/* Underlay: always keep the host/object still for split_screen; hide only for icon meaning diagrams. */}
      {!preferMeaningDiagram ? (
        <MotionLayer
          preset={characterPreset}
          style={{
            position: 'absolute',
            left: imageBox.left,
            top: imageBox.top,
            width: imageBox.width,
            height: imageBox.height,
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'center',
            zIndex: 8,
          }}
        >
          <Img
            src={imageSrc}
            style={{
              width: '100%',
              height: '100%',
              objectFit: imageBox.objectFit,
              objectPosition: iconMode ? 'center center' : 'center bottom',
            }}
          />
        </MotionLayer>
      ) : null}
      {showBoxedHeadline ? (
        <MotionLayer
          preset={textPreset}
          style={{
            position: 'absolute',
            zIndex: 14,
            left: textBox.left,
            right: textBox.right,
            top: textBox.top,
            fontSize: iconMode ? 64 : 72,
            fontWeight: 900,
            lineHeight: 1.05,
            textAlign: textBox.textAlign,
            color: brand.text,
          }}
        >
          <div
            style={{
              padding: '28px 32px',
              borderRadius: 22,
              background: `${brand.panel}EE`,
              border: `2px solid ${brand.accent}99`,
              boxShadow: '0 18px 40px rgba(10,20,30,.35)',
            }}
          >
            <MeasuredText
              text={imageHeadlineFor(scene)}
              width={iconMode ? 872 : 404}
              maxSize={iconMode ? 56 : 44}
              minSize={28}
              maxLines={3}
              align={textBox.textAlign}
            />
          </div>
        </MotionLayer>
      ) : null}
    </SceneShell>
  );
};

type SemanticTheme = {
  panel: string;
  glow: string;
  glowPosition: string;
  border: string;
  shadow: string;
};

export type SemanticOverlayTreatment = 'diagram' | 'floating' | 'none';

export type SemanticComposition = {
  overlayTreatment: SemanticOverlayTreatment;
  overlayTop: number;
  overlayLeft: number;
  overlayScale: number;
  headlineTop: number;
  headlineLeft: number;
  headlineRight: number;
  headlineSize: number;
  headlineAlign: 'left' | 'center' | 'right';
  panFrom: number;
  panTo: number;
};

const DEFAULT_SEMANTIC_COMPOSITION: SemanticComposition = {
  overlayTreatment: 'none',
  overlayTop: 240,
  overlayLeft: 72,
  overlayScale: 0.6,
  headlineTop: 1110,
  headlineLeft: SAFE_X,
  headlineRight: SAFE_X,
  headlineSize: 86,
  headlineAlign: 'left',
  panFrom: -16,
  panTo: 16,
};

const SEMANTIC_COMPOSITIONS: Record<SemanticOverlayKind, SemanticComposition> = {
  reserve_readiness: {...DEFAULT_SEMANTIC_COMPOSITION, overlayTreatment:'diagram', overlayTop:520, overlayScale:1},
  income_split: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 230, overlayLeft: 54, overlayScale: 0.67,
    headlineTop: 1090, panFrom: -22, panTo: 10,
  },
  payday_arrival: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'none', headlineTop: 1020, headlineAlign: 'right', panFrom: 20, panTo: -12,
  },
  convenience_chain: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'floating', overlayTop: 250, overlayLeft: 382, overlayScale: 0.57,
    headlineTop: 1125, panFrom: -10, panTo: 24,
  },
  cumulative_spending: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'none', headlineTop: 1070, headlineAlign: 'center', panFrom: 24, panTo: -18,
  },
  allocation_sorter: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 300, overlayLeft: 190, overlayScale: 0.66,
    headlineTop: 1105, headlineAlign: 'center', panFrom: -24, panTo: 8,
  },
  auto_invest: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'floating', overlayTop: 210, overlayLeft: 420, overlayScale: 0.54,
    headlineTop: 1050, headlineAlign: 'left', panFrom: 18, panTo: -20,
  },
  purchase_pause: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'floating', overlayTop: 540, overlayLeft: 58, overlayScale: 0.55,
    headlineTop: 1035, headlineAlign: 'right', panFrom: -8, panTo: 22,
  },
  raise_absorption: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 160, overlayLeft: 72, overlayScale: 1,
    headlineTop: 1080, headlineAlign: 'center', panFrom: 0, panTo: 0,
  },
  weekly_transfer: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 150, overlayLeft: 72, overlayScale: 1,
    headlineTop: 1080, headlineAlign: 'center', panFrom: 0, panTo: 0,
  },
  notebook_log: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 180, overlayLeft: 72, overlayScale: 1,
    headlineTop: 1080, headlineAlign: 'center', panFrom: 0, panTo: 0,
  },
  habit_repeat: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 160, overlayLeft: 72, overlayScale: 1,
    headlineTop: 1080, headlineAlign: 'center', panFrom: 0, panTo: 0,
  },
  savings_account: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 220, overlayLeft: 54, overlayScale: 0.9,
    headlineTop: 1080, headlineAlign: 'center', panFrom: 0, panTo: 0,
  },
  compound_layers: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 200, overlayLeft: 48, overlayScale: 0.66,
    headlineTop: 1100, headlineAlign: 'left', panFrom: -18, panTo: 12,
  },
  compound_reinvestment: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 245, overlayLeft: 72, overlayScale: 0.66,
    headlineTop: 1100, headlineAlign: 'right', panFrom: 16, panTo: -12,
  },
  compound_time: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 245, overlayLeft: 72, overlayScale: 0.66,
    headlineTop: 1100, headlineAlign: 'center', panFrom: -12, panTo: 16,
  },
  compound_early_start: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 245, overlayLeft: 72, overlayScale: 0.66,
    headlineTop: 1100, headlineAlign: 'left', panFrom: 18, panTo: -10,
  },
  compound_scenario: {
    ...DEFAULT_SEMANTIC_COMPOSITION,
    overlayTreatment: 'diagram', overlayTop: 245, overlayLeft: 72, overlayScale: 0.66,
    headlineTop: 1100, headlineAlign: 'center', panFrom: -10, panTo: 18,
  },
};

export const semanticCompositionFor = (kind: SemanticOverlayKind): SemanticComposition => {
  const composition = SEMANTIC_COMPOSITIONS[kind];
  if (!composition) {
    throw new Error(`Unsupported semantic composition: ${String(kind)}`);
  }
  return composition;
};

const SEMANTIC_THEMES: Record<SemanticOverlayKind, SemanticTheme> = {
  reserve_readiness: {panel:'#F2E8D5',glow:'transparent',glowPosition:'50% 50%',border:'#BC8B48',shadow:'rgba(0,0,0,.2)'},
  income_split: {
    panel: 'linear-gradient(145deg, rgba(28,57,47,.93), rgba(41,68,59,.88))',
    glow: 'rgba(57,255,159,.20)', glowPosition: '18% 30%', border: '#319A72', shadow: 'rgba(9,38,28,.72)',
  },
  payday_arrival: {
    panel: 'linear-gradient(145deg, rgba(67,52,31,.94), rgba(85,64,36,.87))',
    glow: 'rgba(221,174,82,.22)', glowPosition: '84% 22%', border: '#9B7740', shadow: 'rgba(49,32,10,.72)',
  },
  convenience_chain: {
    panel: 'linear-gradient(145deg, rgba(29,49,61,.94), rgba(42,66,76,.88))',
    glow: 'rgba(86,184,219,.20)', glowPosition: '20% 72%', border: '#397C91', shadow: 'rgba(8,30,41,.72)',
  },
  cumulative_spending: {
    panel: 'linear-gradient(145deg, rgba(66,38,40,.94), rgba(83,50,48,.88))',
    glow: 'rgba(211,108,94,.20)', glowPosition: '82% 64%', border: '#945B55', shadow: 'rgba(46,16,18,.72)',
  },
  allocation_sorter: {
    panel: 'linear-gradient(145deg, rgba(35,58,43,.95), rgba(54,72,53,.88))',
    glow: 'rgba(137,199,113,.20)', glowPosition: '24% 24%', border: '#557D56', shadow: 'rgba(15,38,20,.72)',
  },
  auto_invest: {
    panel: 'linear-gradient(145deg, rgba(25,48,58,.95), rgba(35,68,70,.88))',
    glow: 'rgba(70,211,187,.21)', glowPosition: '80% 30%', border: '#3D8F85', shadow: 'rgba(7,30,34,.72)',
  },
  purchase_pause: {
    panel: 'linear-gradient(145deg, rgba(61,48,34,.95), rgba(75,62,44,.88))',
    glow: 'rgba(202,166,91,.20)', glowPosition: '50% 72%', border: '#8B7044', shadow: 'rgba(39,27,11,.72)',
  },
  savings_account: {
    // Paper-cream board — hand-drawn editorial, not chrome UI cards.
    panel: 'linear-gradient(165deg, rgba(240,229,210,.97), rgba(232,220,198,.94))',
    glow: 'rgba(66,101,121,.18)', glowPosition: '70% 35%', border: '#142633', shadow: 'rgba(20,38,51,.45)',
  },
  raise_absorption: {
    panel: 'linear-gradient(165deg, rgba(240,229,210,.97), rgba(232,220,198,.94))',
    glow: 'rgba(66,101,121,.18)', glowPosition: '50% 40%', border: '#142633', shadow: 'rgba(20,38,51,.45)',
  },
  weekly_transfer: {
    panel: 'linear-gradient(165deg, rgba(240,229,210,.97), rgba(228,216,194,.94))',
    glow: 'rgba(66,101,121,.18)', glowPosition: '40% 40%', border: '#142633', shadow: 'rgba(20,38,51,.45)',
  },
  notebook_log: {
    panel: 'linear-gradient(165deg, rgba(240,229,210,.97), rgba(228,216,194,.94))',
    glow: 'rgba(66,101,121,.18)', glowPosition: '45% 40%', border: '#142633', shadow: 'rgba(20,38,51,.45)',
  },
  habit_repeat: {
    panel: 'linear-gradient(165deg, rgba(240,229,210,.97), rgba(228,216,194,.94))',
    glow: 'rgba(66,101,121,.18)', glowPosition: '50% 35%', border: '#142633', shadow: 'rgba(20,38,51,.45)',
  },
  compound_layers: {
    panel: 'linear-gradient(145deg, rgba(20,48,40,.97), rgba(28,62,51,.94))',
    glow: 'rgba(57,255,159,.22)', glowPosition: '20% 35%', border: '#319A72', shadow: 'rgba(9,38,28,.78)',
  },
  compound_reinvestment: {
    panel: 'linear-gradient(145deg, rgba(26,43,48,.97), rgba(31,62,64,.94))',
    glow: 'rgba(70,211,187,.22)', glowPosition: '78% 34%', border: '#3D8F85', shadow: 'rgba(7,30,34,.78)',
  },
  compound_time: {
    panel: 'linear-gradient(145deg, rgba(52,43,27,.97), rgba(67,56,34,.94))',
    glow: 'rgba(221,174,82,.22)', glowPosition: '50% 30%', border: '#9B7740', shadow: 'rgba(49,32,10,.78)',
  },
  compound_early_start: {
    panel: 'linear-gradient(145deg, rgba(24,45,37,.97), rgba(36,65,51,.94))',
    glow: 'rgba(137,199,113,.22)', glowPosition: '20% 60%', border: '#557D56', shadow: 'rgba(15,38,20,.78)',
  },
  compound_scenario: {
    panel: 'linear-gradient(145deg, rgba(31,43,53,.97), rgba(37,62,70,.94))',
    glow: 'rgba(86,184,219,.22)', glowPosition: '76% 54%', border: '#397C91', shadow: 'rgba(8,30,41,.78)',
  },
};

const semanticThemeFor = (kind: SemanticOverlayKind): SemanticTheme => SEMANTIC_THEMES[kind];

const FinanceSemanticOverlay: React.FC<{
  kind: SemanticOverlayKind;
  brand: BrandSpec;
  theme: SemanticTheme;
  composition: SemanticComposition;
  relation?: ManifestScene['visual_relation'];
}> = ({kind, brand, theme, composition, relation}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  // Reveal across most of the scene with ease — not a hard pop in the first second.
  const revealEnd = Math.max(36, Math.floor(durationInFrames * 0.55));
  const reveal = interpolate(frame, [2, revealEnd], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: (t) => 1 - Math.pow(1 - t, 2.4),
  });
  const pulse = 1 + Math.sin(frame / 14) * 0.012;
  const shared = {brand, reveal, pulse, relation};
  const paperBoard =
    kind === 'savings_account' ||
    kind === 'weekly_transfer' ||
    kind === 'raise_absorption' ||
    kind === 'notebook_log' ||
    kind === 'habit_repeat';
  const showFlowChrome = !paperBoard && !kind.startsWith('compound_');
  // Paper boards: no nested chrome card — pencil sketch floats on the navy scene.
  if (paperBoard) {
    return (
      <div
        style={{
          position: 'absolute',
          left: composition.overlayLeft,
          top: composition.overlayTop,
          width: 936,
          height: 1280,
          background: 'transparent',
          border: 'none',
          boxShadow: 'none',
          transform: `scale(${composition.overlayScale})`,
          transformOrigin: 'top left',
          overflow: 'visible',
        }}
      >
        {kind === 'savings_account' ? <SavingsAccountChoice {...shared} /> : null}
        {kind === 'raise_absorption' ? <RaiseAbsorptionFlow {...shared} /> : null}
        {kind === 'weekly_transfer' ? <WeeklyAutoTransfer {...shared} /> : null}
        {kind === 'notebook_log' ? <NotebookLog {...shared} /> : null}
        {kind === 'habit_repeat' ? <HabitRepeat {...shared} /> : null}
      </div>
    );
  }
  return (
    <div
      style={{
        position: 'absolute',
        left: composition.overlayLeft,
        top: composition.overlayTop,
        width: 936,
        height: 760,
        borderRadius: 38,
        background: composition.overlayTreatment === 'diagram'
          ? theme.panel
          : 'linear-gradient(145deg, rgba(10,20,17,.72), rgba(19,30,26,.58))',
        border: composition.overlayTreatment === 'diagram' ? `2px solid ${theme.border}` : `1px solid ${theme.border}99`,
        boxShadow: composition.overlayTreatment === 'diagram'
          ? '0 34px 90px rgba(0,0,0,.55), inset 0 0 80px rgba(57,255,159,.035)'
          : '0 24px 64px rgba(0,0,0,.38)',
        transform: `scale(${composition.overlayScale})`,
        transformOrigin: 'top left',
        overflow: 'hidden',
      }}
    >
      {kind === 'income_split' ? <IncomeSplit {...shared} /> : null}
      {kind === 'payday_arrival' ? <PaydayArrival {...shared} /> : null}
      {kind === 'convenience_chain' ? <ConvenienceChain {...shared} /> : null}
      {kind === 'cumulative_spending' ? <CumulativeSpending {...shared} /> : null}
      {kind === 'allocation_sorter' ? <AllocationSorter {...shared} /> : null}
      {kind === 'auto_invest' ? <AutoInvest {...shared} /> : null}
      {kind === 'purchase_pause' ? <PurchasePause {...shared} /> : null}
      {kind === 'compound_layers' ? <CompoundLayers {...shared} /> : null}
      {kind === 'compound_reinvestment' ? <CompoundReinvestment {...shared} /> : null}
      {kind === 'compound_time' ? <CompoundTime {...shared} /> : null}
      {kind === 'compound_early_start' ? <CompoundEarlyStart {...shared} /> : null}
      {kind === 'compound_scenario' ? <CompoundScenario {...shared} /> : null}
      {showFlowChrome ? <SemanticProgress kind={kind} brand={brand} /> : null}
    </div>
  );
};

const SEMANTIC_SEQUENCE: SemanticOverlayKind[] = [
  'income_split',
  'payday_arrival',
  'convenience_chain',
  'cumulative_spending',
  'allocation_sorter',
  'auto_invest',
  'purchase_pause',
];

const COMPOUND_SEQUENCE: SemanticOverlayKind[] = [
  'compound_layers',
  'compound_reinvestment',
  'compound_time',
  'compound_early_start',
  'compound_scenario',
];

const SemanticProgress: React.FC<{kind: SemanticOverlayKind; brand: BrandSpec}> = ({kind, brand}) => {
  const isCompound = COMPOUND_SEQUENCE.includes(kind);
  const sequence = isCompound ? COMPOUND_SEQUENCE : SEMANTIC_SEQUENCE;
  const activeIndex = sequence.indexOf(kind);
  if (activeIndex < 0) {
    return null;
  }
  return (
    <div style={{position: 'absolute', left: 54, right: 54, bottom: 24, display: 'flex', alignItems: 'center', gap: 22}}>
      <div style={{fontSize: 18, fontWeight: 900, letterSpacing: 4, color: brand.muted, whiteSpace: 'nowrap'}}>
        {isCompound ? 'COMPOUND FLOW' : 'RULE FLOW'} {activeIndex + 1}/{sequence.length}
      </div>
      <div style={{display: 'flex', alignItems: 'center', gap: 10, flex: 1}}>
        {sequence.map((item, index) => (
          <React.Fragment key={item}>
            {index > 0 ? <div style={{height: 2, flex: 1, background: index <= activeIndex ? brand.accent : brand.accentSoft}} /> : null}
            <div
              style={{
                width: index === activeIndex ? 18 : 10,
                height: index === activeIndex ? 18 : 10,
                borderRadius: 999,
                background: index <= activeIndex ? brand.accent : brand.muted,
                opacity: index <= activeIndex ? 1 : 0.42,
                boxShadow: index === activeIndex ? `0 0 18px ${brand.accent}` : undefined,
              }}
            />
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

type OverlayGraphicProps = {
  brand: BrandSpec;
  reveal: number;
  pulse: number;
  relation?: ManifestScene['visual_relation'];
};

const accountLabel = (value: string | null | undefined, fallback: string) =>
  String(value || fallback).replace(/_/g, ' ').toUpperCase();

const IncomeSplit: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="760" viewBox="0 0 936 760" aria-label="Payday income split before any purchase">
    <SemanticTitle brand={brand} eyebrow="PAYDAY RULE" title="SPLIT BEFORE SPENDING" />
    <g opacity={reveal}>
      <Envelope x={356} y={180} brand={brand} />
      <Route path="M468 326 L468 405 C380 430 296 470 218 524" reveal={reveal} brand={brand} />
      <Route path="M468 405 C556 430 640 470 718 524" reveal={reveal} brand={brand} />
      <Coin x={468} y={400} brand={brand} scale={pulse} />
      <ShoppingBasket x={128} y={500} brand={brand} />
      <Bowl x={642} y={510} brand={brand} />
    </g>
  </svg>
);

const PaydayArrival: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="760" viewBox="0 0 936 760" aria-label="A paycheck arrives before spending begins">
    <SemanticTitle brand={brand} eyebrow="MONEY ARRIVES" title="THE CLOCK STARTS" />
    <g opacity={reveal}>
      <Envelope x={120} y={300} brand={brand} />
      <Route path="M330 390 C400 390 430 390 486 390" reveal={reveal} brand={brand} />
      <Wallet x={486} y={320} brand={brand} scale={pulse} />
      <Clock x={692} y={308} brand={brand} />
      <Coin x={446} y={560} brand={brand} scale={pulse} />
    </g>
  </svg>
);

const ConvenienceChain: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="760" viewBox="0 0 936 760" aria-label="Convenience purchases form a chain that drains the wallet">
    <SemanticTitle brand={brand} eyebrow="CONVENIENCE" title="SMALL COSTS GET LOUD" />
    <g opacity={reveal}>
      <DeliveryBag x={80} y={250} brand={brand} />
      <CoffeeCup x={318} y={250} brand={brand} />
      <Parcel x={568} y={270} brand={brand} />
      <Route path="M170 484 C320 540 500 540 660 470" reveal={reveal} brand={brand} />
      <Wallet x={664} y={420} brand={brand} scale={pulse} />
    </g>
  </svg>
);

const CumulativeSpending: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="760" viewBox="0 0 936 760" aria-label="Many small purchases accumulate into a large spending pattern">
    <SemanticTitle brand={brand} eyebrow="PATTERN CHECK" title="SMALL BECOMES LARGE" />
    <g opacity={reveal}>
      {[0, 1, 2, 3, 4].map((index) => (
        <Coin key={index} x={150 + index * 132} y={330 + (index % 2) * 42} brand={brand} scale={index === 4 ? pulse : 0.72} />
      ))}
      <Route path="M154 500 C350 590 560 590 772 492" reveal={reveal} brand={brand} />
      <Bowl x={690} y={485} brand={brand} />
    </g>
  </svg>
);

const AllocationSorter: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="760" viewBox="0 0 936 760" aria-label="Income is assigned to three deliberate destinations">
    <SemanticTitle brand={brand} eyebrow="FRIDAY CHECK" title="ASSIGN EVERY COIN" />
    <g opacity={reveal}>
      <Coin x={468} y={260} brand={brand} scale={pulse} />
      <Gate x={390} y={330} brand={brand} />
      <Route path="M468 430 C360 470 280 500 210 560" reveal={reveal} brand={brand} />
      <Route path="M468 430 L468 570" reveal={reveal} brand={brand} />
      <Route path="M468 430 C576 470 656 500 726 560" reveal={reveal} brand={brand} />
      <Tray x={112} y={560} brand={brand} />
      <Tray x={370} y={580} brand={brand} />
      <Tray x={628} y={560} brand={brand} />
    </g>
  </svg>
);

const AutoInvest: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="760" viewBox="0 0 936 760" aria-label="A spending decision removed from autopilot">
    <SemanticTitle brand={brand} eyebrow="ONE DECISION" title="BREAK THE AUTOPILOT" />
    <g opacity={reveal}>
      <Route path="M468 620 L468 462" reveal={reveal} brand={brand} />
      <Route path="M468 386 C400 330 324 300 232 276" reveal={reveal} brand={brand} />
      <Route path="M468 386 C536 330 612 300 704 276" reveal={reveal} brand={brand} />
      <Coin x={468} y={584} brand={brand} scale={pulse} />
      <Gate x={390} y={388} brand={brand} />
      <ShoppingBasket x={152} y={208} brand={brand} />
      <Vault x={630} y={205} brand={brand} />
      <circle cx="468" cy="386" r="18" fill={brand.accent} />
    </g>
  </svg>
);

const PurchasePause: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="760" viewBox="0 0 936 760" aria-label="A deliberate pause between wallet and purchase">
    <SemanticTitle brand={brand} eyebrow="BEFORE YOU BUY" title="ADD A BORING PAUSE" />
    <g opacity={reveal}>
      <Route path="M236 430 L700 430" reveal={reveal} brand={brand} />
      <Wallet x={126} y={350} brand={brand} scale={1} />
      <Gate x={390} y={352} brand={brand} />
      <ShoppingBasket x={682} y={350} brand={brand} />
      <circle cx="468" cy="430" r={58 * pulse} fill="none" stroke={brand.accent} strokeWidth="5" opacity=".38" />
      <circle cx="468" cy="430" r="19" fill={brand.accent} />
      <text x="468" y="585" textAnchor="middle" fill={brand.text} fontSize="32" fontWeight="900" letterSpacing="5">
        PAUSE / THEN DECIDE
      </text>
    </g>
  </svg>
);

const PaperInk = '#142633';
const PaperMuted = '#426579';
const PaperGold = '#C18A49';

const RaiseAbsorptionFlow: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="1200" viewBox="0 0 936 1200" aria-label="A raise splits into money you spend and money you keep">
    <text x="468" y="64" textAnchor="middle" fill={PaperMuted} fontSize="28" fontWeight="800" letterSpacing="4">
      THE RAISE SPLITS
    </text>
    <g opacity={reveal}>
      <rect x="218" y="110" width="500" height="150" rx="28" fill="#F7F0E4" stroke={PaperInk} strokeWidth="5" />
      <text x="468" y="175" textAnchor="middle" fill={PaperInk} fontSize="40" fontWeight="900">THE RAISE</text>
      <text x="468" y="220" textAnchor="middle" fill={PaperGold} fontSize="24" fontWeight="800" letterSpacing="3">ONE AMOUNT</text>
      <polygon points={`448,${280 - 4 * pulse} 488,${280 - 4 * pulse} 468,320`} fill={PaperGold} />
      <polygon points="300,540 360,620 300,620" fill={PaperGold} />
      <polygon points="636,540 576,620 636,620" fill={PaperInk} />
      <rect x="40" y="640" width="400" height="220" rx="28" fill="#F7F0E4" stroke={PaperInk} strokeWidth="5" />
      <text x="240" y="730" textAnchor="middle" fill={PaperInk} fontSize="40" fontWeight="900">SPEND</text>
      <text x="240" y="790" textAnchor="middle" fill={PaperMuted} fontSize="26" fontWeight="800">THE REST</text>
      <rect x="496" y="640" width="400" height="220" rx="28" fill="#F7F0E4" stroke={PaperGold} strokeWidth="6" />
      <text x="696" y="730" textAnchor="middle" fill={PaperInk} fontSize="40" fontWeight="900">KEEP</text>
      <text x="696" y="790" textAnchor="middle" fill={PaperGold} fontSize="26" fontWeight="800">SAVE FIRST</text>
      <text x="468" y="960" textAnchor="middle" fill={PaperMuted} fontSize="26" fontWeight="800" letterSpacing="2">
        TWO BRANCHES — NOT A SINGLE LINE
      </text>
    </g>
  </svg>
);

const SavingsAccountChoice: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg width="936" height="760" viewBox="0 0 936 760" aria-label="Choose a separate savings account instead of spending from checking">
    <text x="468" y="72" textAnchor="middle" fill={PaperMuted} fontSize="26" fontWeight="800" letterSpacing="6">
      ONE PAYCHECK → TWO POCKETS
    </text>
    <g opacity={reveal}>
      <Wallet x={110} y={230} brand={{...brand, text: PaperInk, accent: PaperGold, muted: PaperMuted}} scale={1.2} />
      <text x="230" y="540" textAnchor="middle" fill={PaperMuted} fontSize="28" fontWeight="900" letterSpacing="2">
        CHECKING · SPEND
      </text>
      <path
        d={`M360 370 C420 ${330 - 10 * pulse} 500 ${330 + 10 * pulse} 560 370`}
        fill="none"
        stroke={PaperGold}
        strokeWidth="6"
        strokeLinecap="round"
        strokeDasharray="14 10"
      />
      <polygon points={`540,350 580,370 540,390`} fill={PaperGold} />
      <g transform="translate(560 200)">
        <ellipse cx="140" cy="300" rx="118" ry="28" fill="#C18A4933" />
        <path
          d="M40 90 C40 40 240 40 240 90 L250 250 C250 320 30 320 40 250 Z"
          fill="#F7F0E4"
          stroke={PaperInk}
          strokeWidth="5"
          strokeLinejoin="round"
        />
        <rect x="70" y="48" width="140" height="36" rx="10" fill="#E8DCC8" stroke={PaperInk} strokeWidth="4" />
        <Coin x={100} y={160} brand={{...brand, accent: PaperGold, text: PaperInk}} scale={0.9 * pulse} />
        <Coin x={150} y={190} brand={{...brand, accent: PaperGold, text: PaperInk}} scale={0.75} />
        <path d="M210 70 L250 40" stroke={PaperGold} strokeWidth="5" strokeLinecap="round" />
        <circle cx="262" cy="32" r="18" fill={PaperGold} stroke={PaperInk} strokeWidth="3" />
        <path d="M252 32 L260 40 L274 22" fill="none" stroke={PaperInk} strokeWidth="4" strokeLinecap="round" />
      </g>
      <text x="700" y="540" textAnchor="middle" fill={PaperInk} fontSize="28" fontWeight="900" letterSpacing="2">
        SAVINGS · KEEP
      </text>
      <text x="468" y="640" textAnchor="middle" fill={PaperMuted} fontSize="24" fontWeight="800" letterSpacing="3">
        SEPARATE ACCOUNT = MONEY YOU DO NOT TOUCH
      </text>
    </g>
  </svg>
);

const WeeklyAutoTransfer: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse, relation}) => {
  const source = accountLabel(relation?.source, 'checking');
  const dest = accountLabel(relation?.destination, 'savings');
  const blocked = relation?.kind === 'transfer_forbidden' || relation?.action === 'block';
  return (
  <svg width="936" height="1200" viewBox="0 0 936 1200" aria-label={`Money moves from ${source} to ${dest}`}>
    <text x="468" y="56" textAnchor="middle" fill={PaperMuted} fontSize="26" fontWeight="800" letterSpacing="4">
      {blocked ? 'TRANSFER BLOCKED' : (relation?.condition || 'TRANSFER').toString().toUpperCase()}
    </text>
    <g opacity={reveal}>
      <rect x="218" y="90" width="500" height="180" rx="28" fill="#F7F0E4" stroke={PaperInk} strokeWidth="5" />
      <text x="468" y="160" textAnchor="middle" fill={PaperInk} fontSize={source.length > 10 ? 32 : 40} fontWeight="900">{source}</text>
      <text x="468" y="215" textAnchor="middle" fill={PaperGold} fontSize="26" fontWeight="800">SOURCE</text>
      {blocked ? (
        <>
          <line x1="420" y1="320" x2="516" y2="480" stroke="#995E4B" strokeWidth="10" />
          <line x1="516" y1="320" x2="420" y2="480" stroke="#995E4B" strokeWidth="10" />
          <text x="468" y="540" textAnchor="middle" fill="#995E4B" fontSize="32" fontWeight="900">NO MOVE</text>
        </>
      ) : (
        <>
          <Route path={`M468 290 C468 ${360 - 10 * pulse} 468 ${420 + 10 * pulse} 468 500`} reveal={reveal} brand={{...brand, accent: PaperGold}} />
          <Coin x={468} y={400} brand={{...brand, accent: PaperGold, text: PaperInk}} scale={pulse} />
        </>
      )}
      <g transform="translate(318 520)">
        <path
          d="M40 90 C40 40 240 40 240 90 L250 280 C250 360 30 360 40 280 Z"
          fill="#F7F0E4"
          stroke={PaperInk}
          strokeWidth="5"
          strokeLinejoin="round"
          opacity={blocked ? 0.35 : 1}
        />
        <rect x="70" y="48" width="140" height="36" rx="10" fill="#E8DCC8" stroke={PaperInk} strokeWidth="4" />
        {!blocked ? <Coin x={110} y={180} brand={{...brand, accent: PaperGold, text: PaperInk}} scale={0.95} /> : null}
        {!blocked ? <Coin x={165} y={210} brand={{...brand, accent: PaperGold, text: PaperInk}} scale={0.8} /> : null}
      </g>
      <text x="468" y="920" textAnchor="middle" fill={PaperInk} fontSize={dest.length > 10 ? 32 : 36} fontWeight="900" letterSpacing="2">
        {dest}
      </text>
      <text x="468" y="980" textAnchor="middle" fill={PaperMuted} fontSize="24" fontWeight="800" letterSpacing="2">
        {blocked ? 'MONEY STAYS IN THE SOURCE' : `${source} → ${dest}`}
      </text>
    </g>
  </svg>
  );
};

const NotebookLog: React.FC<OverlayGraphicProps> = ({reveal, pulse}) => (
  <svg width="936" height="1200" viewBox="0 0 936 1200" aria-label="Notebook records each transfer">
    <text x="468" y="64" textAnchor="middle" fill={PaperMuted} fontSize="28" fontWeight="800" letterSpacing="4">
      WRITE IT DOWN
    </text>
    <g opacity={reveal}>
      <rect x="170" y="140" width="596" height="760" rx="28" fill="#F7F0E4" stroke={PaperInk} strokeWidth="6" />
      <rect x="170" y="140" width="54" height="760" fill="#E8DCC8" stroke={PaperInk} strokeWidth="4" />
      {[0, 1, 2, 3, 4, 5].map((row) => (
        <line key={row} x1="250" y1={260 + row * 90} x2="720" y2={260 + row * 90} stroke="#42657955" strokeWidth="3" />
      ))}
      <text x="260" y="250" fill={PaperMuted} fontSize="28" fontWeight="800">DATE</text>
      <text x="430" y="250" fill={PaperMuted} fontSize="28" fontWeight="800">TRANSFER</text>
      <rect x="250" y={290} width={420 * Math.min(1, reveal * 1.2)} height="54" rx="10" fill={PaperGold} opacity={0.85} />
      <text x="270" y="328" fill={PaperInk} fontSize="30" fontWeight="900" opacity={reveal > 0.35 ? 1 : 0}>
        FRI · CHECKING → SAVINGS
      </text>
      <g transform={`translate(${700 + 8 * pulse} 360)`}>
        <rect x="0" y="0" width="28" height="180" rx="8" fill={PaperInk} transform="rotate(18)" />
        <polygon points="8,180 36,168 20,210" fill={PaperGold} />
      </g>
      <text x="468" y="980" textAnchor="middle" fill={PaperInk} fontSize="28" fontWeight="800">
        ENTRY COMPLETE — PROGRESS VISIBLE
      </text>
    </g>
  </svg>
);

const HabitRepeat: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => {
  const week = Math.min(3, Math.floor(reveal * 3) + 1);
  return (
    <svg width="936" height="1200" viewBox="0 0 936 1200" aria-label="Repeated weekly set-asides accumulate">
      <text x="468" y="64" textAnchor="middle" fill={PaperMuted} fontSize="28" fontWeight="800" letterSpacing="4">
        SAME MOVE · GROWING RESULT
      </text>
      <g opacity={Math.min(1, reveal * 1.2)}>
        {[1, 2, 3].map((n) => (
          <g key={n} opacity={week >= n ? 1 : 0.22}>
            <rect x={80 + (n - 1) * 280} y="180" width="240" height="160" rx="24" fill="#F7F0E4" stroke={PaperInk} strokeWidth="5" />
            <text x={200 + (n - 1) * 280} y="250" textAnchor="middle" fill={PaperInk} fontSize="34" fontWeight="900">
              WEEK {n}
            </text>
            <text x={200 + (n - 1) * 280} y="300" textAnchor="middle" fill={PaperGold} fontSize="24" fontWeight="800">
              SET ASIDE
            </text>
          </g>
        ))}
        <Route path="M200 360 C200 430 468 430 468 500" reveal={reveal} brand={{...brand, accent: PaperGold}} />
        <g transform="translate(318 520)">
          <path
            d="M40 90 C40 40 240 40 240 90 L250 280 C250 360 30 360 40 280 Z"
            fill="#F7F0E4"
            stroke={PaperInk}
            strokeWidth="5"
            strokeLinejoin="round"
          />
          <rect x="70" y="48" width="140" height="36" rx="10" fill="#E8DCC8" stroke={PaperInk} strokeWidth="4" />
          {Array.from({length: week}).map((_, index) => (
            <Coin
              key={index}
              x={100 + index * 36}
              y={200 - index * 18}
              brand={{...brand, accent: PaperGold, text: PaperInk}}
              scale={0.85 + index * 0.05 * pulse}
            />
          ))}
        </g>
        <text x="468" y="920" textAnchor="middle" fill={PaperInk} fontSize="34" fontWeight="900">
          CUSHION AFTER {week} WEEK{week > 1 ? 'S' : ''}
        </text>
        <text x="468" y="980" textAnchor="middle" fill={PaperMuted} fontSize="24" fontWeight="800">
          REPETITION HAS A VISIBLE CONSEQUENCE
        </text>
      </g>
    </svg>
  );
};

const CompoundLayers: React.FC<OverlayGraphicProps> = ({brand, reveal}) => (
  <svg viewBox="0 0 936 700" width="936" height="700">
    <SemanticTitle brand={brand} eyebrow="COMPOUNDING" title="EACH LAYER JOINS THE BASE" />
    <g opacity={reveal}>
      <rect x="168" y="500" width="600" height="104" rx="24" fill="#263C35" stroke={brand.text} strokeWidth="5" />
      <rect x="230" y="376" width="476" height="100" rx="24" fill="#2E7D62" stroke={brand.accent} strokeWidth="5" />
      <rect x="300" y="264" width="336" height="88" rx="22" fill={brand.accent} stroke={brand.text} strokeWidth="5" />
      <text x="468" y="567" textAnchor="middle" fill={brand.text} fontSize="34" fontWeight="900" letterSpacing="4">STARTING MONEY</text>
      <text x="468" y="439" textAnchor="middle" fill={brand.text} fontSize="31" fontWeight="900" letterSpacing="3">PRIOR INTEREST</text>
      <text x="468" y="320" textAnchor="middle" fill="#10251E" fontSize="29" fontWeight="900" letterSpacing="3">NEXT LAYER</text>
    </g>
  </svg>
);

const CompoundReinvestment: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => (
  <svg viewBox="0 0 936 700" width="936" height="700">
    <SemanticTitle brand={brand} eyebrow="NEXT PERIOD" title="GROWTH RETURNS TO THE BASE" />
    <g opacity={reveal}>
      {[0, 1, 2, 3].map((row) => (
        <rect key={row} x={252 - row * 18} y={520 - row * 74} width={360 + row * 36} height="56" rx="28" fill={row === 3 ? brand.accent : '#315C4B'} stroke={brand.text} strokeWidth="4" />
      ))}
      {[0, 1, 2].map((coin) => (
        <circle key={coin} cx={660 + coin * 70} cy={270 + coin * 42} r={34 * pulse} fill="#C8A24A" stroke={brand.text} strokeWidth="5" />
      ))}
      <Route path="M790 360 C814 520 714 616 588 544" reveal={reveal} brand={brand} />
      <text x="468" y="655" textAnchor="middle" fill={brand.muted} fontSize="27" fontWeight="900" letterSpacing="4">A LARGER BASE STARTS THE NEXT CYCLE</text>
    </g>
  </svg>
);

const CompoundTime: React.FC<OverlayGraphicProps> = ({brand, reveal}) => (
  <svg viewBox="0 0 936 700" width="936" height="700">
    <SemanticTitle brand={brand} eyebrow="TIME" title="THE SAME CYCLE REPEATS" />
    <g opacity={reveal}>
      <path d="M126 535H810" stroke={brand.muted} strokeWidth="6" strokeLinecap="round" />
      {[0, 1, 2, 3].map((step) => (
        <g key={step}>
          <circle cx={168 + step * 198} cy="535" r="24" fill={brand.accent} stroke={brand.text} strokeWidth="5" />
          <rect x={130 + step * 198} y={470 - step * 62} width="76" height={45 + step * 62} rx="14" fill={step === 3 ? brand.accent : '#315C4B'} stroke={brand.text} strokeWidth="4" />
        </g>
      ))}
      <text x="468" y="635" textAnchor="middle" fill={brand.muted} fontSize="30" fontWeight="900" letterSpacing="5">PATIENT REPETITION, NOT A ONE-DAY SPIKE</text>
    </g>
  </svg>
);

const CompoundEarlyStart: React.FC<OverlayGraphicProps> = ({brand, reveal}) => (
  <svg viewBox="0 0 936 700" width="936" height="700">
    <SemanticTitle brand={brand} eyebrow="START EARLIER" title="MORE PERIODS, SAME PROCESS" />
    <g opacity={reveal}>
      <text x="76" y="276" fill={brand.accent} fontSize="28" fontWeight="900" letterSpacing="4">EARLY START</text>
      <text x="76" y="474" fill={brand.muted} fontSize="28" fontWeight="900" letterSpacing="4">LATE START</text>
      {[0, 1, 2, 3, 4, 5].map((step) => <circle key={step} cx={244 + step * 92} cy="330" r="24" fill={brand.accent} stroke={brand.text} strokeWidth="4" />)}
      {[0, 1, 2].map((step) => <circle key={step} cx={520 + step * 92} cy="526" r="24" fill="#C8A24A" stroke={brand.text} strokeWidth="4" />)}
      <rect x="790" y="226" width="74" height="128" rx="18" fill={brand.accent} stroke={brand.text} strokeWidth="5" />
      <rect x="790" y="454" width="74" height="72" rx="18" fill="#C8A24A" stroke={brand.text} strokeWidth="5" />
      <path d="M244 330H790M520 526H790" stroke={brand.accentSoft} strokeWidth="5" />
    </g>
  </svg>
);

const CompoundScenario: React.FC<OverlayGraphicProps> = ({brand, reveal, pulse}) => {
  const nodes = [
    {x: 92, label: 'START'},
    {x: 292, label: 'CONTRIBUTE'},
    {x: 520, label: 'WAIT'},
    {x: 712, label: 'REVIEW'},
  ];
  return (
    <svg viewBox="0 0 936 700" width="936" height="700">
      <SemanticTitle brand={brand} eyebrow="BEFORE YOU INVEST" title="TEST ONE LONG-TERM PATH" />
      <g opacity={reveal}>
        <path d="M132 392H804" stroke={brand.accentSoft} strokeWidth="8" strokeLinecap="round" />
        {nodes.map((node, index) => (
          <g key={node.label} transform={`translate(${node.x} 330)`}>
            <rect
              width="132"
              height="124"
              rx="24"
              fill={index === nodes.length - 1 ? brand.accent : '#263C35'}
              stroke={brand.text}
              strokeWidth="5"
              transform={index === 2 ? `translate(${66 * (1 - pulse)} ${62 * (1 - pulse)}) scale(${pulse})` : undefined}
            />
            <text x="66" y="72" textAnchor="middle" fill={index === nodes.length - 1 ? '#10251E' : brand.text} fontSize={node.label.length > 7 ? 17 : 22} fontWeight="900" letterSpacing="2">{node.label}</text>
          </g>
        ))}
        <text x="468" y="590" textAnchor="middle" fill={brand.muted} fontSize="29" fontWeight="900" letterSpacing="4">CHECK THE PROCESS BEFORE RISKING MONEY</text>
      </g>
    </svg>
  );
};

const SemanticTitle: React.FC<{brand: BrandSpec; eyebrow: string; title: string}> = ({brand, eyebrow, title}) => (
  <g>
    <text x="54" y="64" fill={brand.accent} fontSize="21" fontWeight="900" letterSpacing="6">{eyebrow}</text>
    <text x="54" y="116" fill={brand.text} fontSize="42" fontWeight="900" letterSpacing="-1">{title}</text>
  </g>
);

const Route: React.FC<{path: string; reveal: number; brand: BrandSpec}> = ({path, reveal, brand}) => (
  <path
    d={path}
    fill="none"
    stroke={brand.accent}
    strokeWidth="9"
    strokeLinecap="round"
    pathLength="1"
    strokeDasharray=".055 .07"
    strokeDashoffset={1 - reveal}
    opacity=".88"
  />
);

const Wallet: React.FC<{x: number; y: number; brand: BrandSpec; scale: number}> = ({x, y, brand, scale}) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`} style={{transformOrigin: `${x + 100}px ${y + 72}px`}}>
    <rect width="200" height="142" rx="30" fill="#6F4A31" stroke={brand.text} strokeWidth="5" />
    <rect x="118" y="44" width="92" height="58" rx="18" fill="#4B3023" stroke={brand.accent} strokeWidth="5" />
    <circle cx="151" cy="73" r="8" fill={brand.accent} />
  </g>
);

const DeliveryBag: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <path d="M20 54H138L126 170H32Z" fill="#C19A6B" stroke={brand.text} strokeWidth="5" />
    <path d="M50 56C52 10 108 10 110 56" fill="none" stroke={brand.accent} strokeWidth="8" />
  </g>
);

const CoffeeCup: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <path d="M26 42H134L119 170H41Z" fill="#D6D0C4" stroke={brand.text} strokeWidth="5" />
    <path d="M18 42H142" stroke={brand.accent} strokeWidth="12" strokeLinecap="round" />
    <path d="M64 4C48 24 83 23 68 41M102 4C88 24 120 24 106 41" fill="none" stroke={brand.muted} strokeWidth="5" />
  </g>
);

const Sneaker: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <path d="M10 76C80 75 91 20 116 28C151 58 176 71 226 76C252 80 266 104 248 124H28C9 124 0 101 10 76Z" fill="#263C35" stroke={brand.text} strokeWidth="5" />
    <path d="M56 75L126 75M72 58L134 65M24 126H248" fill="none" stroke={brand.accent} strokeWidth="7" />
  </g>
);

const Coin: React.FC<{x: number; y: number; brand: BrandSpec; scale: number}> = ({x, y, brand, scale}) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <circle r="46" fill="#C8A24A" stroke={brand.text} strokeWidth="5" />
    <circle r="28" fill="none" stroke={brand.accent} strokeWidth="5" />
  </g>
);

const Envelope: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <rect width="224" height="138" rx="20" fill="#D8D0BE" stroke={brand.text} strokeWidth="5" />
    <path d="M8 18L112 88L216 18M8 126L82 72M216 126L142 72" fill="none" stroke="#746A58" strokeWidth="7" />
    <circle cx="112" cy="88" r="18" fill={brand.accent} opacity=".86" />
  </g>
);

const Bowl: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <path d="M8 24H180C168 110 138 150 94 150S20 110 8 24Z" fill="#315C4B" stroke={brand.text} strokeWidth="5" />
    <path d="M4 24H184" stroke={brand.accent} strokeWidth="10" strokeLinecap="round" />
    <path d="M94 10V-26M72-4L94-28L116-4" fill="none" stroke={brand.accent} strokeWidth="7" />
  </g>
);

const Clock: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <circle cx="76" cy="76" r="70" fill="#26322E" stroke={brand.text} strokeWidth="6" />
    <path d="M76 30V76L112 96" fill="none" stroke={brand.accent} strokeWidth="8" strokeLinecap="round" />
    <circle cx="76" cy="76" r="8" fill={brand.accent} />
  </g>
);

const Parcel: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <path d="M12 42L94 8L176 42V154L94 190L12 154Z" fill="#8A6845" stroke={brand.text} strokeWidth="5" />
    <path d="M12 42L94 80L176 42M94 80V190M62 23L144 61" fill="none" stroke={brand.accent} strokeWidth="6" />
  </g>
);

const Tray: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <path d="M8 24H188L166 118H30Z" fill="#263C35" stroke={brand.text} strokeWidth="5" />
    <path d="M20 24H176" stroke={brand.accent} strokeWidth="9" strokeLinecap="round" />
  </g>
);

const Gate: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <rect width="18" height="132" rx="9" fill={brand.text} />
    <rect x="138" width="18" height="132" rx="9" fill={brand.text} />
    <rect x="8" y="18" width="140" height="20" rx="10" fill={brand.accent} transform="rotate(-12 8 18)" />
  </g>
);

const ShoppingBasket: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <path d="M14 70H170L148 174H38Z" fill="#342A2A" stroke={brand.text} strokeWidth="5" />
    <path d="M50 72C58 6 128 6 136 72M65 92V156M98 92V156M131 92V156" fill="none" stroke={brand.accent} strokeWidth="7" />
  </g>
);

const Vault: React.FC<{x: number; y: number; brand: BrandSpec}> = ({x, y, brand}) => (
  <g transform={`translate(${x} ${y})`}>
    <rect width="184" height="180" rx="24" fill="#26322E" stroke={brand.text} strokeWidth="6" />
    <circle cx="92" cy="90" r="48" fill="none" stroke={brand.accent} strokeWidth="8" />
    <path d="M92 54V126M56 90H128M67 65L117 115M117 65L67 115" stroke={brand.accent} strokeWidth="6" />
  </g>
);

export const numberLayoutFor = (value: string): {pieces: string[]; fontSize: number} => {
  const pieces = value
    .split(/\s*\/\s*/)
    .map((piece) => piece.trim())
    .filter(Boolean);
  const safePieces = pieces.length > 0 ? pieces : [value];
  const longest = Math.max(...safePieces.map((piece) => piece.length), 1);
  const preferredSize = safePieces.length > 1 ? 200 : 330;
  const widthBoundSize = Math.floor(900 / (longest * 0.68));
  return {pieces: safePieces, fontSize: Math.max(112, Math.min(preferredSize, widthBoundSize))};
};

export type NumberSceneVariant = 'gain' | 'balance' | 'rate' | 'scenario' | 'generic';

export const numberVariantFor = (scene: ManifestScene): NumberSceneVariant => {
  const headline = `${scene.headline ?? ''} ${scene.overlay_text ?? ''}`.toUpperCase();
  const source = `${headline} ${scene.caption}`.toUpperCase();
  // The visual headline is the authoritative variant selector. Narration can
  // mention a prior gain while the current beat is explicitly a risk example.
  if (headline.includes('NOT A PROMISE') || headline.includes('EXAMPLE')) return 'scenario';
  if (headline.includes('BALANCE')) return 'balance';
  if (headline.includes('BASE')) return 'rate';
  if (headline.includes('GAIN') || source.includes('GAIN')) return 'gain';
  return 'generic';
};

export const numberMotionFor = (frame: number, durationInFrames: number) => {
  const end = Math.max(1, durationInFrames - 1);
  const progress = interpolate(frame, [0, end], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return {
    progress,
    entry: interpolate(frame, [0, Math.min(14, end)], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    }),
    drift: Math.sin(progress * Math.PI * 2),
  };
};

const NumberValue: React.FC<{value: string; brand: BrandSpec; compact?: boolean}> = ({value, brand, compact = false}) => {
  const layout = numberLayoutFor(value);
  return (
    <div style={{fontSize: compact ? Math.min(layout.fontSize, 230) : layout.fontSize, lineHeight: 0.86, fontWeight: 900, color: brand.accent}}>
      {layout.pieces.map((piece, index) => (
        <React.Fragment key={`${piece}-${index}`}>
          {index > 0 ? <div style={{fontSize: '0.48em', lineHeight: 0.72}}>/</div> : null}
          <div style={{whiteSpace: 'nowrap'}}>{piece}</div>
        </React.Fragment>
      ))}
    </div>
  );
};

export const NumberScene: React.FC<SceneProps> = ({scene, brand}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const value = numericToken(scene);
  if (!value) {
    throw new Error(`NumberScene ${scene.scene_id} has no source-backed number`);
  }
  const variant = numberVariantFor(scene);
  const motion = numberMotionFor(frame, durationInFrames);
  const headline = headlineFor(scene, 6);
  const variantBackground: Record<NumberSceneVariant, string> = {
    gain: 'radial-gradient(circle at 76% 68%, #1F4741 0%, #142C32 48%, #0C1418 100%)',
    balance: 'linear-gradient(155deg, #494135 0%, #29312A 52%, #101A16 100%)',
    rate: 'radial-gradient(circle at 50% 54%, #1D5058 0%, #15363D 43%, #09161B 100%)',
    scenario: 'linear-gradient(145deg, #3B3327 0%, #2C3329 48%, #0D1816 100%)',
    generic: brand.background,
  };
  const cardStyle: React.CSSProperties = {
    position: 'absolute',
    border: `3px solid ${brand.accentSoft}`,
    background: 'linear-gradient(145deg, rgba(12,35,27,.96), rgba(4,14,11,.90))',
    boxShadow: '0 30px 80px rgba(0,0,0,.32)',
  };
  return (
    <SceneShell scene={scene} brand={brand}>
      <AbsoluteFill style={{zIndex: 0, background: variantBackground[variant]}} />
      {variant === 'gain' ? (
        <>
          <div style={{position: 'absolute', top: 238, left: SAFE_X, width: 650, ...entryTransform(frame)}}>
            <div style={{fontSize: 88, lineHeight: 0.96, fontWeight: 900}}>{headline}</div>
            <div style={{marginTop: 48}}><NumberValue value={value} brand={brand} compact /></div>
          </div>
          <div style={{...cardStyle, top: 770, left: SAFE_X, right: SAFE_X, height: 600, borderRadius: 42, overflow: 'hidden'}}>
            <div style={{position: 'absolute', left: 82, right: 82, bottom: 94, height: 28, borderRadius: 14, background: brand.accentSoft}} />
            {[0, 1, 2].map((stage) => {
              const stageProgress = Math.max(0, Math.min(1, motion.progress * 1.45 - stage * 0.2));
              return (
                <div key={stage} style={{position: 'absolute', left: 106 + stage * 254, bottom: 122, width: 172, height: 145 + stage * 84, borderRadius: 22, border: `5px solid ${brand.text}`, background: `rgba(64, 201, 139, ${0.16 + stageProgress * 0.42})`, transform: `translateY(${(1 - stageProgress) * 180}px)`, opacity: 0.28 + stageProgress * 0.72}}>
                  <div style={{position: 'absolute', left: 14, right: 14, top: -18, height: 28, borderRadius: 14, background: brand.accent, boxShadow: `0 0 ${24 + stageProgress * 34}px ${brand.accentSoft}`}} />
                </div>
              );
            })}
            <div style={{position: 'absolute', right: 70, top: 52, width: 104, height: 104, border: `8px solid ${brand.accent}`, borderRadius: '50%', transform: `rotate(${motion.progress * 280}deg)`}}>
              <div style={{position: 'absolute', left: 42, top: -18, width: 18, height: 44, borderRadius: 9, background: brand.text}} />
            </div>
          </div>
        </>
      ) : variant === 'balance' ? (
        <>
          <div style={{position: 'absolute', top: 250, left: SAFE_X, right: SAFE_X, ...entryTransform(frame)}}>
            <div style={{fontSize: 84, lineHeight: 0.96, fontWeight: 900}}>{headline}</div>
          </div>
          <div style={{...cardStyle, top: 560, left: SAFE_X, right: SAFE_X, height: 770, borderRadius: 54}}>
            <div style={{position: 'absolute', left: 62, top: 92, transform: `translateX(${motion.drift * 10}px)`}}><NumberValue value={value} brand={brand} compact /></div>
            <div style={{position: 'absolute', left: 96, right: 96, bottom: 104, height: 320}}>
              <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 188, borderRadius: 30, border: `5px solid ${brand.text}`, background: 'linear-gradient(90deg, rgba(85,110,101,.54), rgba(42,75,63,.82))'}} />
              <div style={{position: 'absolute', left: 0, width: `${18 + motion.progress * 82}%`, bottom: 188, height: 58, borderRadius: 22, background: brand.accent, boxShadow: `0 0 46px ${brand.accentSoft}`}} />
              {[0, 1, 2, 3].map((tick) => <div key={tick} style={{position: 'absolute', left: `${tick * 33}%`, bottom: -40, width: 4, height: 32, background: brand.muted}} />)}
            </div>
          </div>
        </>
      ) : variant === 'rate' ? (
        <>
          <div style={{position: 'absolute', top: 248, left: SAFE_X, right: SAFE_X, textAlign: 'center', ...entryTransform(frame)}}>
            <div style={{fontSize: 82, lineHeight: 0.96, fontWeight: 900}}>{headline}</div>
          </div>
          <div style={{position: 'absolute', top: 550, left: 170, width: 740, height: 740, transform: `rotate(${motion.drift * 2.5}deg)`}}>
            <div style={{position: 'absolute', inset: 0, borderRadius: '50%', background: `conic-gradient(${brand.accent} ${motion.progress * 360}deg, ${brand.accentSoft} ${motion.progress * 360}deg)`, boxShadow: `0 0 90px ${brand.accentSoft}`}} />
            <div style={{position: 'absolute', inset: 56, borderRadius: '50%', background: brand.background, border: `3px solid ${brand.text}`, display: 'grid', placeItems: 'center', textAlign: 'center'}}>
              <NumberValue value={value} brand={brand} compact />
            </div>
            {[0, 1, 2].map((token) => {
              const angle = motion.progress * Math.PI * 2 + token * ((Math.PI * 2) / 3);
              return <div key={token} style={{position: 'absolute', left: 350 + Math.cos(angle) * 330, top: 350 + Math.sin(angle) * 330, width: 54, height: 54, margin: -27, borderRadius: '50%', background: token === 0 ? brand.text : brand.accent, border: `6px solid ${brand.background}`}} />;
            })}
          </div>
        </>
      ) : variant === 'scenario' ? (
        <>
          <div style={{position: 'absolute', top: 240, left: SAFE_X, right: SAFE_X, ...entryTransform(frame)}}>
            <div style={{fontSize: 84, lineHeight: 0.96, fontWeight: 900}}>{headline}</div>
          </div>
          <div style={{...cardStyle, top: 585, left: SAFE_X, right: SAFE_X, height: 690, borderRadius: 26, borderColor: '#C99949', transform: `translateX(${motion.drift * 8}px)`}}>
            <div style={{position: 'absolute', left: 54, top: 86}}><NumberValue value={value} brand={brand} compact /></div>
            <div style={{position: 'absolute', right: 78, top: 92, width: 174, height: 220}}>
              <div style={{position: 'absolute', left: 34, top: 0, width: 106, height: 106, border: `14px solid ${brand.text}`, borderBottom: 0, borderRadius: '70px 70px 0 0', transform: `translateY(${(1 - motion.progress) * 24}px)`}} />
              <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 130, borderRadius: 20, background: '#C99949', border: `6px solid ${brand.text}`}} />
              <div style={{position: 'absolute', left: 76, bottom: 34, width: 22, height: 54, borderRadius: 11, background: brand.background}} />
            </div>
            <div style={{position: 'absolute', left: 58, right: 58, bottom: 82, height: 188, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 22}}>
              {[0, 1, 2].map((block) => <div key={block} style={{borderRadius: 18, border: `4px solid ${block === 2 ? '#C99949' : brand.accentSoft}`, background: `rgba(255,255,255,${0.04 + block * 0.025})`, transform: `translateY(${(1 - motion.progress) * (70 + block * 26)}px)`, opacity: 0.4 + motion.progress * 0.6}} />)}
            </div>
          </div>
        </>
      ) : (
        <div style={{position: 'absolute', top: 330, left: SAFE_X, right: SAFE_X, textAlign: 'center', transform: `scale(${0.78 + motion.entry * 0.22})`}}>
          <NumberValue value={value} brand={brand} />
          <div style={{marginTop: 54, fontSize: 92, lineHeight: 1.02, fontWeight: 900}}>{headline}</div>
        </div>
      )}
    </SceneShell>
  );
};

export const ChartScene: React.FC<SceneProps> = ({scene, brand}) => {
  if (scene.verified_series) return <VerifiedCompoundChart scene={scene} brand={brand}/>;
  if (scene.comparison_left || scene.comparison_right) {
    return <ComparisonChartLayout scene={scene} brand={brand} />;
  }
  return <LineChartLayout scene={scene} brand={brand} />;
};

const LineChartLayout: React.FC<SceneProps> = ({scene, brand}) => {
  const frame = useCurrentFrame();
  const values = chartValues(scene);
  if (values.length < 2) {
    throw new Error(`ChartScene ${scene.scene_id} needs at least two source-backed values`);
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  const pointCoordinates = values.map((value, index) => {
    const x = 72 + (index / (values.length - 1)) * 792;
    const y = 550 - ((value - min) / range) * 390;
    return {x, y, value};
  });
  const points = pointCoordinates.map(({x, y}) => `${x},${y}`);
  // Never invent temporal semantics. Labels are rendered only when the
  // source-backed production manifest provides one for every value.
  const labels = scene.chart_labels?.length === values.length ? scene.chart_labels : [];
  const currency = /\$/.test(`${scene.caption} ${scene.headline ?? ''} ${scene.overlay_text ?? ''}`);
  const reveal = interpolate(frame, [4, 30], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <SceneShell scene={scene} brand={brand}>
      <div style={{position: 'absolute', top: 260, left: SAFE_X, right: SAFE_X}}>
        <div style={{fontSize: 88, lineHeight: 1, fontWeight: 900}}>{headlineFor(scene, 7)}</div>
        <div style={{marginTop: 68, padding: 42, borderRadius: 36, background: brand.panel, border: `2px solid ${brand.accentSoft}`}}>
          <svg width="936" height="620" viewBox="0 0 936 620">
            {[0, 1, 2, 3, 4].map((line) => (
              <line key={line} x1="52" x2="884" y1={90 + line * 112} y2={90 + line * 112} stroke={brand.accentSoft} strokeWidth="2" />
            ))}
            <polyline
              points={points.join(' ')}
              fill="none"
              stroke={brand.accent}
              strokeWidth="16"
              strokeLinecap="round"
              strokeLinejoin="round"
              pathLength="1"
              strokeDasharray="1"
              strokeDashoffset={1 - reveal}
            />
            {pointCoordinates.map(({x, y, value}, index) => (
              <g key={`${value}-${index}`} opacity={reveal}>
                <circle cx={x} cy={y} r="18" fill={brand.accent} stroke={brand.text} strokeWidth="7" />
                <text x={x} y={Math.max(54, y - 34)} textAnchor="middle" fill={brand.text} fontSize="34" fontWeight="900">
                  {chartValueLabel(value, currency)}
                </text>
                <text x={x} y="605" textAnchor="middle" fill={brand.muted} fontSize="22" fontWeight="900" letterSpacing="2">
                  {labels[index]}
                </text>
              </g>
            ))}
          </svg>
        </div>
      </div>
    </SceneShell>
  );
};

const ComparisonChartLayout: React.FC<SceneProps> = ({scene, brand}) => {
  const frame = useCurrentFrame();
  const left = scene.comparison_left?.trim();
  const right = scene.comparison_right?.trim();
  if (!left || !right) {
    throw new Error(`ChartScene ${scene.scene_id} comparison needs two explicit labels`);
  }
  const reveal = interpolate(frame, [0, 18], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const values = chartValues(scene);
  const leftValue = values[0];
  const rightValue = values[1];
  return (
    <SceneShell scene={scene} brand={brand}>
      <MotionLayer preset="TEXT_FADE" style={{position: 'absolute', top: 245, left: SAFE_X, right: SAFE_X}}>
        <div style={{fontSize: 72, lineHeight: 0.98, fontWeight: 900, color: brand.text}}>{headlineFor(scene, 7)}</div>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 28, marginTop: 64}}>
          <div
            style={{
              height: 760,
              padding: '42px 34px',
              borderRadius: 28,
              background: brand.panel,
              border: `2px solid ${brand.accentSoft}`,
              opacity: reveal,
            }}
          >
            <div style={{fontSize: 28, lineHeight: 1.08, fontWeight: 900, color: brand.accent, letterSpacing: 2}}>
              {left.toUpperCase()}
            </div>
            <div style={{marginTop: 120, fontSize: 96, fontWeight: 900, color: brand.text}}>
              {Number.isFinite(leftValue) ? chartValueLabel(leftValue, false) : '—'}
            </div>
            <div
              style={{
                marginTop: 80,
                height: 18,
                borderRadius: 999,
                background: `${brand.accentSoft}55`,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${Math.min(100, 40 + reveal * 45)}%`,
                  height: '100%',
                  background: brand.accent,
                }}
              />
            </div>
          </div>
          <div
            style={{
              height: 760,
              padding: '42px 34px',
              borderRadius: 28,
              background: brand.panel,
              border: `2px solid ${brand.muted}`,
              opacity: reveal,
            }}
          >
            <div style={{fontSize: 28, lineHeight: 1.08, fontWeight: 900, color: brand.muted, letterSpacing: 2}}>
              {right.toUpperCase()}
            </div>
            <div style={{marginTop: 120, fontSize: 96, fontWeight: 900, color: brand.text}}>
              {Number.isFinite(rightValue) ? chartValueLabel(rightValue, false) : '—'}
            </div>
            <div
              style={{
                marginTop: 80,
                height: 18,
                borderRadius: 999,
                background: `${brand.muted}44`,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${Math.min(100, 25 + reveal * 35)}%`,
                  height: '100%',
                  background: brand.muted,
                }}
              />
            </div>
          </div>
        </div>
      </MotionLayer>
    </SceneShell>
  );
};

export const CTAScene: React.FC<SceneProps> = ({scene, brand}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const y = interpolate(frame, [0, 12], [42, 0], {extrapolateRight: 'clamp'});
  const progress = interpolate(frame, [0, Math.max(1, durationInFrames - 1)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const hasTimedCaptions = Boolean(scene.caption_words && scene.caption_words.length > 0);
  const layerReveal = interpolate(frame, [4, 28], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const raiseFlow = scene.semantic_overlay_kind === 'raise_absorption';
  const planningDeskClosing = !raiseFlow && (brand.illustrationSystem === 'wb_planning_desk_v1'
    || brand.illustrationSystem === 'wb_planning_desk_painted_v1');
  const stack = raiseFlow
    ? [
        {label: 'OLD PAY', fill: brand.panel, ink: brand.text},
        {label: 'KEEP A SLICE', fill: '#2A4A5C', ink: brand.text},
        {label: 'SPEND THE REST', fill: brand.accent, ink: '#142633'},
      ]
    : [
        {label: 'EVERY PAYDAY', fill: brand.panel, ink: brand.text},
        {label: 'KEEP A SLICE', fill: '#2A4A5C', ink: brand.text},
        {label: 'REPEAT', fill: brand.accent, ink: '#142633'},
      ];
  return (
    <SceneShell scene={scene} brand={brand} showBrandMark={!planningDeskClosing}>
      {!planningDeskClosing && brand.presentation !== 'educational_illustration' ? <MotionLayer
        preset="TEXT_FADE"
        style={{position: 'absolute', top: 220, left: SAFE_X, right: SAFE_X, textAlign: 'center', transform: `translateY(${y}px)`}}
      >
        <div style={{fontSize: 32, color: brand.accent, fontWeight: 900, letterSpacing: 6}}>
          {raiseFlow ? 'ONE CHOICE' : 'WITH INTENTION'}
        </div>
        <div style={{marginTop: 28, color: brand.text}}>
          <MeasuredText
            text={headlineFor(scene, 6) || (raiseFlow ? 'WHERE NEXT RAISE GOES' : 'WEALTH BLUEPRINT')}
            width={936}
            maxSize={hasTimedCaptions ? 64 : 80}
            minSize={36}
            maxLines={3}
            align="center"
          />
        </div>
      </MotionLayer> : null}
      {/* Sketchbook stack — mechanism CTA avoids hard-coded COMPOUND for raise briefs */}
      {raiseFlow ? <div style={{position: 'absolute', left: SAFE_X, right: SAFE_X, top: 520, display: 'flex', justifyContent: 'center'}}>
        <svg width="820" height="520" viewBox="0 0 820 520" aria-hidden="true">
          <g opacity={0.35 + layerReveal * 0.65}>
            <rect x="90" y="320" width="640" height="110" rx="22" fill={stack[2].fill} stroke={brand.text} strokeWidth="5" />
            <rect
              x="140"
              y="220"
              width="540"
              height="100"
              rx="22"
              fill={stack[1].fill}
              stroke={brand.accent}
              strokeWidth="5"
              transform={`translate(0 ${(1 - layerReveal) * 24})`}
            />
            <rect
              x="200"
              y="130"
              width="420"
              height="90"
              rx="20"
              fill={stack[0].fill}
              stroke={brand.text}
              strokeWidth="5"
              transform={`translate(0 ${(1 - layerReveal) * 40})`}
            />
            <text x="410" y="388" textAnchor="middle" fill={stack[2].ink} fontSize="30" fontWeight="900" letterSpacing="3">
              {stack[2].label}
            </text>
            <text x="410" y="282" textAnchor="middle" fill={stack[1].ink} fontSize="28" fontWeight="900" letterSpacing="3">
              {stack[1].label}
            </text>
            <text x="410" y="188" textAnchor="middle" fill={stack[0].ink} fontSize="28" fontWeight="900" letterSpacing="3">
              {stack[0].label}
            </text>
          </g>
        </svg>
      </div> : <div data-wb-cta={planningDeskClosing ? 'planning-desk-closing' : undefined}
        style={{position: 'absolute', left: SAFE_X, right: SAFE_X, top: planningDeskClosing ? 740 : 640, textAlign: 'center', opacity: layerReveal}}>
        <div style={{color: brand.accent, fontSize: 32, fontWeight: 700, letterSpacing: 5}}>FOLLOW</div>
        <div style={{marginTop: 28, color: brand.text}}>
          <MeasuredText text={brand.wordmark} width={936} maxSize={84} minSize={54} maxLines={2} align="center" />
        </div>
        <div style={{marginTop: 44, color: brand.text, fontSize: 34, lineHeight: 1.5}}>Build wealth on purpose.</div>
      </div>}
      {!hasTimedCaptions && !planningDeskClosing ? (
        <div
          style={{
            position: 'absolute',
            left: '50%',
            bottom: 320,
            transform: 'translateX(-50%)',
            width: 620,
            padding: '34px 28px',
            borderRadius: 28,
            background: brand.accent,
            color: brand.background,
            fontSize: 34,
            fontWeight: 900,
            letterSpacing: 2,
            textAlign: 'center',
            opacity: 0.55 + progress * 0.45,
          }}
        >
          SAVE THIS IDEA
        </div>
      ) : null}
    </SceneShell>
  );
};
