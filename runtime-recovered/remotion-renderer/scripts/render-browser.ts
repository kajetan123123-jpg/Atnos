import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {readFile, stat} from 'node:fs/promises';
import {extname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {pathToFileURL} from 'node:url';
import {bundle} from '@remotion/bundler';
import {chromium} from 'playwright-core';
import {
  DEFAULT_BRAND,
  durationInFrames,
  FPS,
  HEIGHT,
  RENDERER_VERSION,
  type RendererProps,
  validateManifest,
  WIDTH,
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

const dataUri = (path: string, bytes: Buffer, mime?: string): string =>
  `data:${mime ?? mimeFor(path)};base64,${bytes.toString('base64')}`;

const sha256File = async (path: string): Promise<string> =>
  createHash('sha256').update(await readFile(path)).digest('hex');

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

const findBrowser = (): string => {
  const candidates = [
    process.env.ATNOS_REMOTION_BROWSER,
    process.env.PROGRAMFILES ? `${process.env.PROGRAMFILES}\\Google\\Chrome\\Application\\chrome.exe` : undefined,
    process.env['PROGRAMFILES(X86)']
      ? `${process.env['PROGRAMFILES(X86)']}\\Microsoft\\Edge\\Application\\msedge.exe`
      : undefined,
  ];
  const browser = candidates.find((candidate): candidate is string => Boolean(candidate && existsSync(candidate)));
  if (!browser) throw new Error('A signed Chrome or Edge executable is required');
  return browser;
};

const writeFrame = async (stream: NodeJS.WritableStream, bytes: Buffer): Promise<void> => {
  if (!stream.write(bytes)) await once(stream, 'drain');
};

const waitForExit = async (child: ReturnType<typeof spawn>): Promise<number> => {
  const [code] = (await once(child, 'exit')) as [number | null];
  return code ?? -1;
};

const main = async () => {
  const manifestPath = valueFor('--manifest');
  const outputPath = valueFor('--output');
  const ffmpeg = process.env.ATNOS_FFMPEG;
  if (!ffmpeg || !existsSync(ffmpeg)) throw new Error('ATNOS_FFMPEG must point to a real ffmpeg executable');
  const manifest = validateManifest(JSON.parse(await readFile(manifestPath, 'utf8')));
  const frames = durationInFrames(manifest);
  process.stderr.write('ATNOS_REMOTION_BROWSER_STAGE=manifest_validated\n');

  const assetData: Record<string, string> = {};
  for (const scene of manifest.scenes) {
    for (const asset of scene.assets) {
      if (asset.role !== 'selected_image' || asset.qa?.status !== 'passed' || !asset.exists || !existsSync(asset.path)) continue;
      const bytes = await readFile(asset.path);
      const actualHash = createHash('sha256').update(bytes).digest('hex');
      if (!asset.provenance?.artifact_hash || asset.provenance.artifact_hash !== actualHash) {
        throw new Error(`Selected image hash mismatch: ${asset.asset_id}`);
      }
      assetData[asset.asset_id] = dataUri(asset.path, bytes);
    }
  }
  const regularFont = findFont(['Montserrat-Bold.ttf', 'montserrat-bold.ttf', 'arialbd.ttf']);
  const extraBoldFont = findFont(['Montserrat-ExtraBold.ttf', 'montserrat-extrabold.ttf', 'arialbd.ttf']);
  if (!regularFont || !extraBoldFont) throw new Error('Deterministic renderer fonts are unavailable');
  const props: RendererProps = {
    manifest,
    assetData,
    brand: manifest.brand ?? DEFAULT_BRAND,
    fontRegularDataUri: dataUri(regularFont, await readFile(regularFont), 'font/ttf'),
    fontExtraBoldDataUri: dataUri(extraBoldFont, await readFile(extraBoldFont), 'font/ttf'),
  };

  const rendererRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
  const sourceFiles = [
    'src/ResearchIntroScene.tsx',
    'src/ProfessionalResearchScene.tsx',
    'src/EditorialMotionScene.tsx',
    'src/EditorialPlanningScene.tsx',
    'src/editorial-motion.ts',
    'src/StyleComparisonScene.tsx',
    'package.json', 'pnpm-lock.yaml', 'scripts/render.ts', 'scripts/render-browser.ts',
    'src/browser-player.tsx', 'src/index.ts', 'src/Root.tsx', 'src/contracts.ts',
    'src/design.tsx', 'src/scenes.tsx', 'src/WealthBlueprintShort.tsx',
    'src/VisualStoryScene.tsx', 'src/ResearchCheckScene.tsx', 'src/ReserveReadinessScene.tsx', 'src/ReserveObjects.tsx', 'src/WBEditorialWorld.tsx', 'src/WBPlanningDeskScene.tsx', 'src/WBPlanningDeskHands.tsx', 'src/WBPaintedMaterials.tsx', 'src/typography.tsx',
  ];
  const sourceHashes = Object.fromEntries(
    (await Promise.all(sourceFiles.map(async (relative) => [relative, await sha256File(resolve(rendererRoot, relative))] as const)))
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0)),
  );
  const sourceFingerprint = createHash('sha256').update(JSON.stringify(sourceHashes)).digest('hex');
  const fontAssets = {
    regular: {filename: regularFont.split(/[\\/]/).pop(), sha256: await sha256File(regularFont)},
    extra_bold: {filename: extraBoldFont.split(/[\\/]/).pop(), sha256: await sha256File(extraBoldFont)},
  };

  process.stderr.write('ATNOS_REMOTION_BROWSER_STAGE=bundle_started\n');
  const serveUrl = await bundle({
    entryPoint: resolve(rendererRoot, 'src/browser-player.tsx'),
    ignoreRegisterRootWarning: true,
  });
  const playerUrl = pathToFileURL(resolve(serveUrl, 'index.html')).toString();
  const browser = await chromium.launch({
    executablePath: findBrowser(),
    headless: true,
    args: ['--disable-gpu', '--hide-scrollbars', '--force-color-profile=srgb'],
  });
  try {
    const page = await browser.newPage({viewport: {width: WIDTH, height: HEIGHT}, deviceScaleFactor: 1});
    const pageDiagnostics: string[] = [];
    page.on('console', (message) => pageDiagnostics.push(`console.${message.type()}: ${message.text()}`));
    page.on('pageerror', (error) => pageDiagnostics.push(`pageerror: ${error.message}`));
    await page.addInitScript((input: RendererProps) => {
      window.__ATNOS_RENDER_PROPS__ = input;
    }, props);
    await page.goto(playerUrl, {waitUntil: 'networkidle', timeout: 120_000});
    try {
      await page.waitForFunction(() => window.__ATNOS_RENDER_READY__ === true, undefined, {timeout: 30_000});
    } catch (error) {
      const browserState = await page.evaluate(() => ({
        title: document.title,
        body: document.body?.innerText?.slice(0, 1000) ?? '',
        hasRoot: Boolean(document.getElementById('atnos-browser-render-root')),
        ready: window.__ATNOS_RENDER_READY__,
        renderError: window.__ATNOS_RENDER_ERROR__,
      }));
      throw new Error(
        `Remotion Player did not become ready: ${String(error)}; ` +
          `state=${JSON.stringify(browserState)}; diagnostics=${pageDiagnostics.slice(-20).join(' | ')}`,
      );
    }
    process.stderr.write('ATNOS_REMOTION_BROWSER_STAGE=player_ready\n');

    const encoder = spawn(
      ffmpeg,
      [
        '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', 'pipe:0',
        '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart', outputPath,
      ],
      {stdio: ['pipe', 'ignore', 'pipe'], windowsHide: true},
    );
    let encoderError = '';
    encoder.stderr?.on('data', (chunk: Buffer | string) => {
      encoderError += Buffer.from(chunk).toString('utf8');
    });
    for (let frame = 0; frame < frames; frame += 1) {
      await page.evaluate(async (targetFrame: number) => {
        if (!window.atnosRenderFrame) throw new Error('Frame renderer is unavailable');
        await window.atnosRenderFrame(targetFrame);
      }, frame);
      const errors = pageDiagnostics.filter((entry) => entry.startsWith('pageerror:'));
      if (errors.length) throw new Error(`Frame ${frame}: ${errors.join(' | ')}`);
      const screenshot = await page.screenshot({type: 'png', animations: 'disabled'});
      if (!encoder.stdin) throw new Error('FFmpeg stdin is unavailable');
      await writeFrame(encoder.stdin, screenshot);
      if (frame % 30 === 0 || frame === frames - 1) {
        process.stderr.write(`ATNOS_REMOTION_BROWSER_PROGRESS=${frame + 1}/${frames}\n`);
      }
    }
    encoder.stdin?.end();
    const exitCode = await waitForExit(encoder);
    if (exitCode !== 0) throw new Error(`FFmpeg exited ${exitCode}: ${encoderError.slice(-2000)}`);
  } finally {
    await browser.close();
  }

  const outputStat = await stat(outputPath);
  if (outputStat.size <= 4096) throw new Error(`Rendered MP4 is too small: ${outputStat.size} bytes`);
  process.stdout.write(
    `\nATNOS_REMOTION_RESULT=${JSON.stringify({
      ok: true,
      renderer_version: RENDERER_VERSION,
      render_backend: 'signed-browser-player',
      output: outputPath,
      bytes: outputStat.size,
      duration_in_frames: frames,
      fps: FPS,
      width: WIDTH,
      height: HEIGHT,
      manifest_content_hash: manifest.content_hash,
      output_sha256: await sha256File(outputPath),
      renderer_source_hashes: sourceHashes,
      renderer_source_fingerprint: sourceFingerprint,
      font_assets: fontAssets,
    })}\n`,
  );
};

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  process.stderr.write(`ATNOS_REMOTION_BROWSER_ERROR=${message}\n`);
  process.exit(1);
});
