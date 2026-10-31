import React from 'react';
import {Composition} from 'remotion';
import {
  AnimationShowcase,
  SHOWCASE_COMPOSITION_ID,
  SHOWCASE_FPS,
  SHOWCASE_HEIGHT,
  SHOWCASE_WIDTH,
  showcaseDurationInFrames,
} from './AnimationShowcase';
import {
  DataVizShowcase,
  DATAVIZ_SHOWCASE_COMPOSITION_ID,
  DATAVIZ_SHOWCASE_FPS,
  DATAVIZ_SHOWCASE_HEIGHT,
  DATAVIZ_SHOWCASE_WIDTH,
  dataVizShowcaseDurationInFrames,
} from './dataviz';
import {
  COMPOSITION_ID,
  DEFAULT_BRAND,
  durationInFrames,
  FPS,
  HEIGHT,
  RENDERER_VERSION,
  type RendererProps,
  validateManifest,
  WIDTH,
} from './contracts';
import {WealthBlueprintShort} from './WealthBlueprintShort';

const defaultProps: RendererProps = {
  manifest: {
    schema_version: '1.0',
    manifest_id: 'preview-manifest',
    job_id: 'preview-job',
    project_id: 'wealth-blueprint',
    channel: 'wealth_blueprint',
    language_code: 'en',
    brand_profile_version: 'preview',
    renderer_version: RENDERER_VERSION,
    content_hash: '0'.repeat(64),
    scenes: [
      {
        scene_id: 's01',
        scene_type: 'HookScene',
        timing: {start_sec: 0, end_sec: 2},
        narration: 'Your money needs a system.',
        caption: 'Your money needs a system.',
        headline: 'Your money needs a system',
        assets: [],
      },
    ],
  },
  assetData: {},
  brand: DEFAULT_BRAND,
};

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id={COMPOSITION_ID}
      component={WealthBlueprintShort}
      durationInFrames={60}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      defaultProps={defaultProps}
      calculateMetadata={({props}) => {
        const manifest = validateManifest(props.manifest);
        return {
          durationInFrames: durationInFrames(manifest),
          fps: FPS,
          width: WIDTH,
          height: HEIGHT,
        };
      }}
    />
    <Composition
      id={SHOWCASE_COMPOSITION_ID}
      component={AnimationShowcase}
      durationInFrames={showcaseDurationInFrames()}
      fps={SHOWCASE_FPS}
      width={SHOWCASE_WIDTH}
      height={SHOWCASE_HEIGHT}
    />
    <Composition
      id={DATAVIZ_SHOWCASE_COMPOSITION_ID}
      component={DataVizShowcase}
      durationInFrames={dataVizShowcaseDurationInFrames()}
      fps={DATAVIZ_SHOWCASE_FPS}
      width={DATAVIZ_SHOWCASE_WIDTH}
      height={DATAVIZ_SHOWCASE_HEIGHT}
    />
  </>
);
