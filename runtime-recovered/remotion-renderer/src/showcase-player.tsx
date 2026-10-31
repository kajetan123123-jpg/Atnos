import React, {useEffect, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Player, type PlayerRef} from '@remotion/player';
import {
  AnimationShowcase,
  SHOWCASE_COMPOSITION_ID,
  SHOWCASE_FPS,
  SHOWCASE_HEIGHT,
  SHOWCASE_WIDTH,
  showcaseDurationInFrames,
} from './AnimationShowcase';

declare global {
  interface Window {
    __ATNOS_SHOWCASE_READY__?: boolean;
    __ATNOS_SHOWCASE_ERROR__?: string;
    __ATNOS_SHOWCASE_META__?: {
      compositionId: string;
      fps: number;
      width: number;
      height: number;
      durationInFrames: number;
    };
    atnosRenderFrame?: (frame: number) => Promise<void>;
  }
}

const ShowcasePlayer: React.FC = () => {
  const player = useRef<PlayerRef>(null);
  const durationInFrames = showcaseDurationInFrames();

  useEffect(() => {
    window.__ATNOS_SHOWCASE_META__ = {
      compositionId: SHOWCASE_COMPOSITION_ID,
      fps: SHOWCASE_FPS,
      width: SHOWCASE_WIDTH,
      height: SHOWCASE_HEIGHT,
      durationInFrames,
    };
    window.atnosRenderFrame = async (frame: number) => {
      const ref = player.current;
      if (!ref) throw new Error('Showcase player is not mounted');
      ref.pause();
      ref.seekTo(frame);
      await new Promise<void>((resolvePromise) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolvePromise())),
      );
    };
    window.__ATNOS_SHOWCASE_READY__ = true;
    return () => {
      window.__ATNOS_SHOWCASE_READY__ = false;
      delete window.atnosRenderFrame;
    };
  }, [durationInFrames]);

  return (
    <Player
      ref={player}
      component={AnimationShowcase}
      durationInFrames={durationInFrames}
      fps={SHOWCASE_FPS}
      compositionWidth={SHOWCASE_WIDTH}
      compositionHeight={SHOWCASE_HEIGHT}
      controls={false}
      autoPlay={false}
      initiallyShowControls={false}
      style={{width: SHOWCASE_WIDTH, height: SHOWCASE_HEIGHT, display: 'block'}}
    />
  );
};

document.documentElement.style.margin = '0';
document.documentElement.style.width = `${SHOWCASE_WIDTH}px`;
document.documentElement.style.height = `${SHOWCASE_HEIGHT}px`;
document.body.style.margin = '0';
document.body.style.width = `${SHOWCASE_WIDTH}px`;
document.body.style.height = `${SHOWCASE_HEIGHT}px`;
document.body.style.overflow = 'hidden';
document.body.style.background = '#111111';

const root = document.createElement('div');
root.id = 'atnos-showcase-root';
root.style.width = `${SHOWCASE_WIDTH}px`;
root.style.height = `${SHOWCASE_HEIGHT}px`;
document.body.appendChild(root);
createRoot(root).render(<ShowcasePlayer />);
