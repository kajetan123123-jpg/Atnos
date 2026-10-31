import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdir, writeFile, stat} from 'node:fs/promises';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {pathToFileURL} from 'node:url';
import {bundle} from '@remotion/bundler';
import {chromium} from 'playwright-core';
import {animationPresetCatalog, listAnimationPresets} from '../src/motion';
import {
  SHOWCASE_COMPOSITION_ID,
  SHOWCASE_FPS,
  SHOWCASE_HEIGHT,
  SHOWCASE_WIDTH,
  showcaseDurationInFrames,
} from '../src/AnimationShowcase';

const args = process.argv.slice(2);
const valueFor = (name: string, fallback?: string): string => {
  const index = args.indexOf(name);
  if (index === -1 || !args[index + 1]) {
    if (fallback) return fallback;
    throw new Error(`Missing required argument ${name}`);
  }
  return resolve(args[index + 1]);
};

const findBrowser = (): string => {
  const candidates = [
    process.env.ATNOS_REMOTION_BROWSER,
    process.env.PLAYWRIGHT_BROWSERS_PATH
      ? resolve(process.env.PLAYWRIGHT_BROWSERS_PATH, 'chromium-1187', 'chrome-win', 'chrome.exe')
      : undefined,
    'E:\\Atnos\\Tools\\playwright-browsers\\chromium-1187\\chrome-win\\chrome.exe',
    process.env.PROGRAMFILES ? `${process.env.PROGRAMFILES}\\Google\\Chrome\\Application\\chrome.exe` : undefined,
    process.env['PROGRAMFILES(X86)']
      ? `${process.env['PROGRAMFILES(X86)']}\\Microsoft\\Edge\\Application\\msedge.exe`
      : undefined,
    process.env.LOCALAPPDATA
      ? `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`
      : undefined,
  ];
  const browser = candidates.find((candidate): candidate is string => Boolean(candidate && existsSync(candidate)));
  if (!browser) throw new Error('A signed Chrome or Edge executable is required (set ATNOS_REMOTION_BROWSER)');
  return browser;
};

const resolveFfmpeg = (): string => {
  if (process.env.ATNOS_FFMPEG && existsSync(process.env.ATNOS_FFMPEG)) {
    return process.env.ATNOS_FFMPEG;
  }
  return 'ffmpeg';
};

const writeFrame = async (stream: NodeJS.WritableStream, bytes: Buffer): Promise<void> => {
  if (!stream.write(bytes)) await once(stream, 'drain');
};

const waitForExit = async (child: ReturnType<typeof spawn>): Promise<number> => {
  const [code] = (await once(child, 'exit')) as [number | null];
  return code ?? -1;
};

const main = async () => {
  const rendererRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
  const outputPath = valueFor(
    '--output',
    resolve(rendererRoot, 'demos/animation-showcase/animation_presets_showcase.mp4'),
  );
  const catalogPath = resolve(rendererRoot, 'src/motion/animation_presets_catalog.json');
  const frames = showcaseDurationInFrames();
  const ffmpeg = resolveFfmpeg();

  process.stderr.write(`ATNOS_MOTION_SHOWCASE=start presets=${listAnimationPresets().length} frames=${frames}\n`);
  await mkdir(dirname(outputPath), {recursive: true});
  await writeFile(catalogPath, `${JSON.stringify(animationPresetCatalog(), null, 2)}\n`, 'utf8');

  const serveUrl = await bundle({
    entryPoint: resolve(rendererRoot, 'src/showcase-player.tsx'),
    ignoreRegisterRootWarning: true,
  });
  const playerUrl = pathToFileURL(resolve(serveUrl, 'index.html')).toString();
  const browser = await chromium.launch({
    executablePath: findBrowser(),
    headless: true,
    args: ['--disable-gpu', '--hide-scrollbars', '--force-color-profile=srgb'],
  });

  try {
    const page = await browser.newPage({
      viewport: {width: SHOWCASE_WIDTH, height: SHOWCASE_HEIGHT},
      deviceScaleFactor: 1,
    });
    await page.goto(playerUrl, {waitUntil: 'networkidle', timeout: 120_000});
    await page.waitForFunction(() => window.__ATNOS_SHOWCASE_READY__ === true, undefined, {timeout: 60_000});
    process.stderr.write('ATNOS_MOTION_SHOWCASE=player_ready\n');

    const encoder = spawn(
      ffmpeg,
      [
        '-y',
        '-loglevel',
        'error',
        '-f',
        'image2pipe',
        '-framerate',
        String(SHOWCASE_FPS),
        '-i',
        'pipe:0',
        '-an',
        '-c:v',
        'libx264',
        '-preset',
        'veryfast',
        '-crf',
        '18',
        '-pix_fmt',
        'yuv420p',
        '-movflags',
        '+faststart',
        outputPath,
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
      const screenshot = await page.screenshot({type: 'png', animations: 'disabled'});
      if (!encoder.stdin) throw new Error('FFmpeg stdin is unavailable');
      await writeFrame(encoder.stdin, screenshot);
      if (frame % 48 === 0 || frame === frames - 1) {
        process.stderr.write(`ATNOS_MOTION_SHOWCASE_PROGRESS=${frame + 1}/${frames}\n`);
      }
    }
    encoder.stdin?.end();
    const exitCode = await waitForExit(encoder);
    if (exitCode !== 0) {
      throw new Error(`FFmpeg exited ${exitCode}: ${encoderError.slice(-2000)}`);
    }
  } finally {
    await browser.close();
  }

  const outputStat = await stat(outputPath);
  if (outputStat.size <= 4096) {
    throw new Error(`Showcase MP4 is too small: ${outputStat.size} bytes`);
  }
  const sha256 = createHash('sha256').update(await (await import('node:fs/promises')).readFile(outputPath)).digest('hex');
  const marker = {
    ok: true,
    composition_id: SHOWCASE_COMPOSITION_ID,
    output: outputPath,
    bytes: outputStat.size,
    sha256,
    fps: SHOWCASE_FPS,
    width: SHOWCASE_WIDTH,
    height: SHOWCASE_HEIGHT,
    duration_frames: frames,
    duration_sec: frames / SHOWCASE_FPS,
    presets: listAnimationPresets().length,
    catalog: catalogPath,
  };
  process.stdout.write(`${JSON.stringify(marker)}\n`);
  process.stderr.write('ATNOS_MOTION_SHOWCASE=done\n');
};

main().catch((error) => {
  process.stderr.write(`ATNOS_MOTION_SHOWCASE_ERROR=${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
