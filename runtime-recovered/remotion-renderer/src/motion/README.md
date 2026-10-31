# Motion preset library (Atnos Remotion)

Deterministic animation presets live in `src/motion/`. Atnos selects a **preset id + parameters**; it does not generate Remotion/TSX animation code.

Presets are registered once in `registry.ts`. UI uses `MotionLayer` — do not copy keyframes between components.

## Registry API

```ts
import {
  listAnimationPresets,
  getAnimationPreset,
  sampleAnimation,
  MotionLayer,
  animationPresetCatalog,
} from './src/motion';
```

Each preset exposes defaults: `duration`, `delay`, `easing`, `intensity`, `direction`.

Catalog JSON: `src/motion/animation_presets_catalog.json` (written by showcase render).

## Showcase film

Vertical 1080×1920 @ 30fps, every preset labeled on screen:

`demos/animation-showcase/animation_presets_showcase.mp4`

```powershell
$env:PATH = "E:\Atnos\Tools\NodeJS;" + $env:PATH
$env:ATNOS_FFMPEG = "E:\Atnos\App\runtime-recovered\api\.venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
$env:ATNOS_REMOTION_BROWSER = "E:\Atnos\Tools\playwright-browsers\chromium-1187\chrome-win\chrome.exe"
cd E:\Atnos\App\runtime-recovered\remotion-renderer
node ./node_modules/tsx/dist/cli.mjs scripts/render-showcase-browser.ts
```

## Tests

```powershell
node ./node_modules/vitest/vitest.mjs run tests/motion-presets.test.ts
```
