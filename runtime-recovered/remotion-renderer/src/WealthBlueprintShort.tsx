import React from 'react';
import {AbsoluteFill, Sequence} from 'remotion';
import type {ManifestScene, RendererProps} from './contracts';
import {DEFAULT_BRAND, sceneFrameRange, selectImageAsset, validateManifest} from './contracts';
import {FontLoader} from './design';
import {ChartScene, CTAScene, HookScene, ImageScene, NumberScene} from './scenes';
import {VisualStoryScene} from './VisualStoryScene';
import {ResearchCheckScene} from './ResearchCheckScene';
import {ResearchIntroScene} from './ResearchIntroScene';
import {ProfessionalResearchScene} from './ProfessionalResearchScene';
import {StyleComparisonScene} from './StyleComparisonScene';
import {EditorialMotionScene} from './EditorialMotionScene';

const SceneComponent: React.FC<{
  scene: ManifestScene;
  props: RendererProps;
}> = ({scene, props}) => {
  const brand = {...DEFAULT_BRAND, ...props.brand};
  if(brand.comparisonStyle) return <StyleComparisonScene scene={scene} brand={brand}/>;
  const subjects=props.manifest.scenes.filter(s=>s.visual_relation?.kind==='research_check')
    .map(s=>s.visual_relation?.objects?.find(o=>o.role==='subject')?.label??'').filter(Boolean);
  if(brand.visualVariant==='editorial_motion') return <EditorialMotionScene scene={scene} brand={brand} subjects={subjects}/>;
  if(brand.visualVariant) return <ProfessionalResearchScene scene={scene} brand={brand} subjects={subjects} assetData={props.assetData}/>;
  if(scene.scene_type==='HookScene'&&scene.scene_id===props.manifest.scenes[0]?.scene_id
      &&subjects.length===3&&/\bstocks?\b/i.test(scene.narration))
    return <ResearchIntroScene scene={scene} brand={brand} subjects={subjects}/>;
  if (scene.visual_relation?.kind === 'research_check') return <ResearchCheckScene scene={scene} brand={brand} />;
  if (scene.visual_story) return <VisualStoryScene scene={scene} brand={brand} assetData={props.assetData} />;
  if (scene.scene_type === 'HookScene') return <HookScene scene={scene} brand={brand} />;
  if (scene.scene_type === 'ImageScene') {
    return <ImageScene scene={scene} brand={brand} imageSrc={selectImageAsset(scene, props.assetData)} />;
  }
  if (scene.scene_type === 'NumberScene') return <NumberScene scene={scene} brand={brand} />;
  if (scene.scene_type === 'ChartScene') return <ChartScene scene={scene} brand={brand} />;
  return <CTAScene scene={scene} brand={brand} />;
};

export const WealthBlueprintShort: React.FC<RendererProps> = (props) => {
  const manifest = validateManifest(props.manifest);
  const brand = {...DEFAULT_BRAND, ...(manifest.brand ?? {}), ...props.brand};
  return (
    <AbsoluteFill style={{background: brand.background}}>
      <FontLoader regular={props.fontRegularDataUri} extraBold={props.fontExtraBoldDataUri} />
      {manifest.scenes.map((scene, index) => {
        const {from, durationInFrames} = sceneFrameRange(scene, index === manifest.scenes.length - 1);
        return (
          <Sequence key={scene.scene_id} from={from} durationInFrames={durationInFrames} premountFor={10}>
            <SceneComponent scene={scene} props={{...props, brand}} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
