import React, {useEffect, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Player, type PlayerRef} from '@remotion/player';
import {
  DataVizShowcase,
  DATAVIZ_SHOWCASE_COMPOSITION_ID,
  DATAVIZ_SHOWCASE_FPS,
  DATAVIZ_SHOWCASE_HEIGHT,
  DATAVIZ_SHOWCASE_WIDTH,
  dataVizShowcaseDurationInFrames,
} from './dataviz';

declare global {
  interface Window {
    __ATNOS_DATAVIZ_READY__?: boolean;
    __ATNOS_DATAVIZ_ERROR__?: string;
    __ATNOS_DATAVIZ_META__?: {
      compositionId: string;
      fps: number;
      width: number;
      height: number;
      durationInFrames: number;
    };
    atnosRenderFrame?: (frame: number) => Promise<void>;
  }
}

const DataVizPlayer: React.FC = () => {
  const player = useRef<PlayerRef>(null);
  const durationInFrames = dataVizShowcaseDurationInFrames();

  useEffect(() => {
    window.__ATNOS_DATAVIZ_META__ = {
      compositionId: DATAVIZ_SHOWCASE_COMPOSITION_ID,
      fps: DATAVIZ_SHOWCASE_FPS,
      width: DATAVIZ_SHOWCASE_WIDTH,
      height: DATAVIZ_SHOWCASE_HEIGHT,
      durationInFrames,
    };
    window.atnosRenderFrame = async (frame: number) => {
      const ref = player.current;
      if (!ref) throw new Error('DataViz player is not mounted');
      ref.pause();
      ref.seekTo(frame);
      await new Promise<void>((resolvePromise) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolvePromise())),
      );
    };
    window.__ATNOS_DATAVIZ_READY__ = true;
    return () => {
      window.__ATNOS_DATAVIZ_READY__ = false;
      delete window.atnosRenderFrame;
    };
  }, [durationInFrames]);

  return (
    <Player
      ref={player}
      component={DataVizShowcase}
      durationInFrames={durationInFrames}
      fps={DATAVIZ_SHOWCASE_FPS}
      compositionWidth={DATAVIZ_SHOWCASE_WIDTH}
      compositionHeight={DATAVIZ_SHOWCASE_HEIGHT}
      controls={false}
      autoPlay={false}
      initiallyShowControls={false}
      style={{width: DATAVIZ_SHOWCASE_WIDTH, height: DATAVIZ_SHOWCASE_HEIGHT, display: 'block'}}
    />
  );
};

document.documentElement.style.margin = '0';
document.documentElement.style.width = `${DATAVIZ_SHOWCASE_WIDTH}px`;
document.documentElement.style.height = `${DATAVIZ_SHOWCASE_HEIGHT}px`;
document.body.style.margin = '0';
document.body.style.width = `${DATAVIZ_SHOWCASE_WIDTH}px`;
document.body.style.height = `${DATAVIZ_SHOWCASE_HEIGHT}px`;
document.body.style.overflow = 'hidden';
document.body.style.background = '#111111';

const root = document.createElement('div');
root.id = 'atnos-dataviz-root';
root.style.width = `${DATAVIZ_SHOWCASE_WIDTH}px`;
root.style.height = `${DATAVIZ_SHOWCASE_HEIGHT}px`;
document.body.appendChild(root);
createRoot(root).render(<DataVizPlayer />);
