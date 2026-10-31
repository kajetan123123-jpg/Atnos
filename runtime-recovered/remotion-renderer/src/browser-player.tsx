import React, {useEffect, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Player, type PlayerRef} from '@remotion/player';
import {durationInFrames, FPS, HEIGHT, type RendererProps, validateManifest, WIDTH} from './contracts';
import {WealthBlueprintShort} from './WealthBlueprintShort';

declare global {
  interface Window {
    __ATNOS_RENDER_PROPS__?: RendererProps;
    __ATNOS_RENDER_READY__?: boolean;
    __ATNOS_RENDER_ERROR__?: string;
    atnosRenderFrame?: (frame: number) => Promise<void>;
  }
}

const BrowserFramePlayer: React.FC<{props: RendererProps}> = ({props}) => {
  const player = useRef<PlayerRef>(null);
  const manifest = validateManifest(props.manifest);

  useEffect(() => {
    window.atnosRenderFrame = async (frame: number) => {
      const ref = player.current;
      if (!ref) throw new Error('Remotion Player is not mounted');
      ref.pause();
      ref.seekTo(frame);
      await new Promise<void>((resolvePromise) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolvePromise())),
      );
      await document.fonts.ready;
      for (let attempts=0; document.querySelector('[data-wb-measuring]'); attempts++) {
        if (attempts > 120) throw new Error('WB text measurement did not settle');
        await new Promise<void>((done) => requestAnimationFrame(() => done()));
      }
      await Promise.all(Array.from(document.images).map((img) => img.decode()));
      for (const node of Array.from(document.querySelectorAll<HTMLElement>('[data-wb-measured-text]'))) {
        const box = node.getBoundingClientRect();
        for (const line of Array.from(node.children)) {
          const range = document.createRange();
          range.selectNodeContents(line);
          const ink = range.getBoundingClientRect();
          if (ink.left < box.left - 1 || ink.right > box.right + 1) {
            throw new Error(`WB text overflow at frame ${frame}: ${node.dataset.wbMeasuredText}`);
          }
        }
        if (box.left < 0 || box.right > WIDTH + 1 || box.top < 0 || box.bottom > HEIGHT + 1) {
          throw new Error(`WB text outside frame ${frame}: ${node.dataset.wbMeasuredText}`);
        }
      }
    };
    window.__ATNOS_RENDER_READY__ = true;
    return () => {
      window.__ATNOS_RENDER_READY__ = false;
      delete window.atnosRenderFrame;
    };
  }, []);

  return (
    <Player
      ref={player}
      component={WealthBlueprintShort}
      inputProps={props}
      durationInFrames={durationInFrames(manifest)}
      fps={FPS}
      compositionWidth={WIDTH}
      compositionHeight={HEIGHT}
      controls={false}
      autoPlay={false}
      initiallyShowControls={false}
      style={{width: WIDTH, height: HEIGHT, display: 'block'}}
    />
  );
};

const props = window.__ATNOS_RENDER_PROPS__;
if (!props) {
  window.__ATNOS_RENDER_ERROR__ = 'Renderer props were not injected';
  throw new Error(window.__ATNOS_RENDER_ERROR__);
}

document.documentElement.style.margin = '0';
document.documentElement.style.width = `${WIDTH}px`;
document.documentElement.style.height = `${HEIGHT}px`;
document.body.style.margin = '0';
document.body.style.width = `${WIDTH}px`;
document.body.style.height = `${HEIGHT}px`;
document.body.style.overflow = 'hidden';
document.body.style.background = '#080B10';

const root = document.createElement('div');
root.id = 'atnos-browser-render-root';
root.style.width = `${WIDTH}px`;
root.style.height = `${HEIGHT}px`;
document.body.appendChild(root);
createRoot(root).render(<BrowserFramePlayer props={props} />);
