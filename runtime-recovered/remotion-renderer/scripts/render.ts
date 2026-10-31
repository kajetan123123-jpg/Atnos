import {mkdtemp, readFile, rm, stat} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {tmpdir} from 'node:os';
import {extname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {bundle} from '@remotion/bundler';
import {renderFrames, renderMedia, selectComposition} from '@remotion/renderer';
import {
  COMPOSITION_ID,
  RENDERER_VERSION,
  type RendererProps,
  validateManifest,
} from '../src/contracts';

const args = process.argv.slice(2);
const valueFor = (name: string): string => {
  const index = args.indexOf(name);
  if (index === -1 || !args[index + 1]) throw new Error(`Missing required argument ${name}`);
  return resolve(args[index + 1]);
};

const mimeFor = (path: string): string => {
  const extension = extname(path).toLowerCase();
  if (extension === '.png') return 'image/png';
  if (extension === '.webp') return 'image/webp';
  if (extension === '.gif') return 'image/gif';
  return 'image/jpeg';
};

const dataUri = async (path: string, mime?: string): Promise<string> => {
  const bytes = await readFile(path);
  return `data:${mime ?? mimeFor(path)};base64,${bytes.toString('base64')}`;
};

const sha256File = async (path: string): Promise<string> =>
  createHash('sha256').update(await readFile(path)).digest('hex');

const dataUriFromBytes = (path: string, bytes: Buffer, mime?: string): string =>
  `data:${mime ?? mimeFor(path)};base64,${bytes.toString('base64')}`;

const readStdin = async (): Promise<string> =>
  new Promise((resolvePromise, reject) => {
    const chunks: Buffer[] = [];
    process.stdin.on('data', (chunk: Buffer | string) => chunks.push(Buffer.from(chunk)));
    process.stdin.on('end', () => resolvePromise(Buffer.concat(chunks).toString('utf8')));
    process.stdin.on('error', reject);
  });

const findFont = (names: string[]): string | null => {
  const roots = [process.env.ATNOS_FONTS_DIR, 'C:\\Windows\\Fonts'].filter(Boolean) as string[];
  for (const root of roots) {
    for (const name of names) {
      const candidate = resolve(root, name);
      if (existsSync(candidate)) return candidate;
    }
  }
  return null;
};

const findBrowser = (): string | undefined => {
  const candidates = [
    process.env.ATNOS_REMOTION_BROWSER,
    process.env.PROGRAMFILES ? `${process.env.PROGRAMFILES}\\Google\\Chrome\\Application\\chrome.exe` : undefined,
    process.env['PROGRAMFILES(X86)']
      ? `${process.env['PROGRAMFILES(X86)']}\\Microsoft\\Edge\\Application\\msedge.exe`
      : undefined,
  ];
  return candidates.find((candidate): candidate is string => Boolean(candidate && existsSync(candidate)));
};

const encodePngSequence = async (ffmpeg: string, frameDir: string, outputPath: string, frameCount: number): Promise<void> => {
  // Remotion pads from-zero frame indexes to the width of the last frame.
  const frameIndexWidth = String(Math.max(0, frameCount - 1)).length;
  const inputPattern = join(frameDir, `element-%0${frameIndexWidth}d.png`);
  const args = [
    '-y', '-progress', 'pipe:2', '-nostats', '-framerate', '30', '-start_number', '0', '-i', inputPattern,
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
    '-r', '30', '-movflags', '+faststart', outputPath,
  ];
  await new Promise<void>((resolvePromise, reject) => {
    const encoder = spawn(ffmpeg, args, {windowsHide: true});
    const stderr: Buffer[] = [];
    let progressBuffer = '';
    encoder.stderr.on('data', (chunk: Buffer | string) => {
      const bytes = Buffer.from(chunk);
      stderr.push(bytes);
      progressBuffer += bytes.toString('utf8');
      const lines = progressBuffer.split(/\r?\n/);
      progressBuffer = lines.pop() ?? '';
      for (const line of lines) {
        const match = /^frame=(\d+)$/.exec(line.trim());
        if (match) process.stderr.write(`ATNOS_REMOTION_PROGRESS=encode:${match[1]}/${frameCount}\n`);
      }
    });
    encoder.once('error', reject);
    encoder.once('close', (code) => {
      if (code === 0) {
        resolvePromise();
        return;
      }
      reject(new Error(`External FFmpeg failed with code ${String(code)}: ${Buffer.concat(stderr).toString('utf8').slice(-2400)}`));
    });
  });
};

const main = async () => {
  const outputPath = valueFor('--output');
  const manifestJson = args.includes('--manifest-stdin')
    ? await readStdin()
    : args.includes('--manifest')
      ? await readFile(valueFor('--manifest'), 'utf8')
      : null;
  if (manifestJson === null) throw new Error('Manifest must be supplied through --manifest or --manifest-stdin');
  const manifest = validateManifest(JSON.parse(manifestJson));
  process.stderr.write('ATNOS_REMOTION_STAGE=manifest_validated\n');
  const assetData: Record<string, string> = {};
  for (const scene of manifest.scenes) {
    for (const asset of scene.assets) {
      if (asset.role !== 'selected_image' || asset.qa?.status !== 'passed' || !asset.exists || !existsSync(asset.path)) continue;
      const assetBytes = await readFile(asset.path);
      const actualHash = createHash('sha256').update(assetBytes).digest('hex');
      if (!asset.provenance?.artifact_hash || asset.provenance.artifact_hash !== actualHash) {
        throw new Error(`Selected image hash mismatch: ${asset.asset_id}`);
      }
      assetData[asset.asset_id] = dataUriFromBytes(asset.path, assetBytes);
    }
  }
  const regularFont = findFont(['Montserrat-Bold.ttf', 'montserrat-bold.ttf', 'arialbd.ttf']);
  const extraBoldFont = findFont(['Montserrat-ExtraBold.ttf', 'montserrat-extrabold.ttf', 'arialbd.ttf']);
  if (!regularFont || !extraBoldFont) throw new Error('Deterministic renderer fonts are unavailable');
  const inputProps: RendererProps = {
    manifest,
    assetData,
    brand: manifest.brand,
    fontRegularDataUri: regularFont ? await dataUri(regularFont, 'font/ttf') : undefined,
    fontExtraBoldDataUri: extraBoldFont ? await dataUri(extraBoldFont, 'font/ttf') : undefined,
  };
  process.stderr.write('ATNOS_REMOTION_STAGE=assets_loaded\n');
  const rendererRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
  const styleFiles = [
    'package.json',
    'pnpm-lock.yaml',
    'scripts/render.ts',
    'scripts/render-browser.ts',
    'src/browser-player.tsx',
    'src/index.ts',
    'src/Root.tsx',
    'src/contracts.ts',
    'src/design.tsx',
    'src/scenes.tsx',
    'src/WealthBlueprintShort.tsx',
    'src/VisualStoryScene.tsx',
    'src/ResearchCheckScene.tsx',
    'src/ResearchIntroScene.tsx',
    'src/ProfessionalResearchScene.tsx',
    'src/EditorialMotionScene.tsx',
    'src/EditorialPlanningScene.tsx',
    'src/editorial-motion.ts',
    'src/StyleComparisonScene.tsx',
    'src/ReserveReadinessScene.tsx',
    'src/ReserveObjects.tsx',
    'src/WBEditorialWorld.tsx',
    'src/WBPlanningDeskScene.tsx',
    'src/WBPlanningDeskHands.tsx',
    'src/WBPaintedMaterials.tsx',
    'src/typography.tsx',
  ];
  const unsortedRendererSourceHashes = Object.fromEntries(
    await Promise.all(styleFiles.map(async (relative) => [relative, await sha256File(resolve(rendererRoot, relative))])),
  );
  const rendererSourceHashes = Object.fromEntries(
    Object.entries(unsortedRendererSourceHashes).sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0)),
  );
  const rendererSourceFingerprint = createHash('sha256')
    .update(JSON.stringify(rendererSourceHashes))
    .digest('hex');
  const fontAssets = {
    regular: {filename: regularFont.split(/[\\/]/).pop(), sha256: await sha256File(regularFont)},
    extra_bold: {filename: extraBoldFont.split(/[\\/]/).pop(), sha256: await sha256File(extraBoldFont)},
  };
  process.stderr.write('ATNOS_REMOTION_STAGE=bundle_started\n');
  const serveUrl = await bundle({entryPoint: resolve(rendererRoot, 'src/index.ts')});
  process.stderr.write('ATNOS_REMOTION_STAGE=bundle_completed\n');
  const browserExecutable = findBrowser();
  const composition = await selectComposition({
    serveUrl,
    id: COMPOSITION_ID,
    inputProps,
    browserExecutable,
    chromeMode: 'chrome-for-testing',
  });
  process.stderr.write('ATNOS_REMOTION_STAGE=composition_selected\n');
  const externalFfmpeg = process.env.ATNOS_FFMPEG;
  if (externalFfmpeg && existsSync(externalFfmpeg)) {
    const frameDir = await mkdtemp(join(tmpdir(), 'atnos-remotion-frames-'));
    try {
      process.stderr.write('ATNOS_REMOTION_STAGE=external_frame_render_started\n');
      await renderFrames({
        composition,
        serveUrl,
        inputProps,
        outputDir: frameDir,
        imageFormat: 'png',
        muted: true,
        concurrency: 1,
        browserExecutable,
        chromeMode: 'chrome-for-testing',
        timeoutInMilliseconds: 120_000,
        logLevel: 'warn',
        onStart: () => process.stderr.write('ATNOS_REMOTION_STAGE=frame_render_started\n'),
        onFrameUpdate: (framesRendered, _frameIndex) => {
          if (framesRendered === 1 || framesRendered % 30 === 0 || framesRendered === composition.durationInFrames) {
            process.stderr.write(`ATNOS_REMOTION_PROGRESS=${framesRendered}/${composition.durationInFrames}\n`);
          }
        },
      });
      process.stderr.write('ATNOS_REMOTION_STAGE=external_frame_render_completed\n');
      process.stderr.write('ATNOS_REMOTION_STAGE=external_ffmpeg_started\n');
      await encodePngSequence(externalFfmpeg, frameDir, outputPath, composition.durationInFrames);
      process.stderr.write('ATNOS_REMOTION_STAGE=external_ffmpeg_completed\n');
    } finally {
      await rm(frameDir, {recursive: true, force: true});
    }
  } else {
    process.stderr.write('ATNOS_REMOTION_STAGE=media_render_started\n');
    await renderMedia({
      composition,
      serveUrl,
      codec: 'h264',
      outputLocation: outputPath,
      inputProps,
      muted: true,
      imageFormat: 'png',
      pixelFormat: 'yuv420p',
      crf: 18,
      x264Preset: 'veryfast',
      concurrency: 1,
      disallowParallelEncoding: true,
      browserExecutable,
      chromeMode: 'chrome-for-testing',
      overwrite: true,
      timeoutInMilliseconds: 120_000,
      onProgress: ({renderedFrames, encodedFrames, progress}) => {
        process.stderr.write(`ATNOS_REMOTION_PROGRESS=media:${renderedFrames}/${composition.durationInFrames}:${encodedFrames}:${progress.toFixed(3)}\n`);
      },
      logLevel: 'warn',
    });
  }
  process.stderr.write('ATNOS_REMOTION_STAGE=media_render_completed\n');
  process.stderr.write('ATNOS_REMOTION_STAGE=media_rendered\n');
  const outputStat = await stat(outputPath);
  if (outputStat.size <= 4096) throw new Error(`Rendered MP4 is too small: ${outputStat.size} bytes`);
  const outputSha256 = await sha256File(outputPath);
  process.stdout.write(
    `\nATNOS_REMOTION_RESULT=${JSON.stringify({
      ok: true,
      renderer_version: RENDERER_VERSION,
      output: outputPath,
      bytes: outputStat.size,
      duration_in_frames: composition.durationInFrames,
      fps: composition.fps,
      width: composition.width,
      height: composition.height,
      manifest_content_hash: manifest.content_hash,
      output_sha256: outputSha256,
      renderer_source_hashes: rendererSourceHashes,
      renderer_source_fingerprint: rendererSourceFingerprint,
      font_assets: fontAssets,
    })}\n`,
  );
};

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  process.stderr.write(`ATNOS_REMOTION_ERROR=${message}\n`);
  process.exit(1);
});
