/**
 * Render a single Remotion frame PNG from a ProjectManifest (visual alignment evidence).
 * Stays on the existing WealthBlueprintShort composition — no second engine.
 */
import {readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {extname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderStill, selectComposition} from '@remotion/renderer';
import {
  COMPOSITION_ID,
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

const dataUriFromBytes = (path: string, bytes: Buffer, mime?: string): string =>
  `data:${mime ?? mimeFor(path)};base64,${bytes.toString('base64')}`;

const dataUri = async (path: string, mime?: string): Promise<string> => {
  const bytes = await readFile(path);
  return dataUriFromBytes(path, bytes, mime);
};

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

const main = async () => {
  const outputPath = valueFor('--output');
  const manifestPath = valueFor('--manifest');
  const frameArg = args.includes('--frame') ? Number(args[args.indexOf('--frame') + 1]) : 30;
  const manifest = validateManifest(JSON.parse(await readFile(manifestPath, 'utf8')));
  const assetData: Record<string, string> = {};
  for (const scene of manifest.scenes) {
    for (const asset of scene.assets) {
      if (asset.role !== 'selected_image' || asset.qa?.status !== 'passed' || !asset.exists || !existsSync(asset.path)) {
        continue;
      }
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
    fontRegularDataUri: await dataUri(regularFont, 'font/ttf'),
    fontExtraBoldDataUri: await dataUri(extraBoldFont, 'font/ttf'),
  };
  const rendererRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
  const serveUrl = await bundle({entryPoint: resolve(rendererRoot, 'src/index.ts')});
  const browserExecutable = findBrowser();
  if (!browserExecutable) throw new Error('A signed installed Chrome or Edge is required for the still preview');
  const composition = await selectComposition({serveUrl, id: COMPOSITION_ID, inputProps, browserExecutable, chromeMode: 'chrome-for-testing'});
  const frame = Math.min(Math.max(0, frameArg), Math.max(0, composition.durationInFrames - 1));
  await renderStill({
    composition,
    serveUrl,
    output: outputPath,
    inputProps,
    frame,
    imageFormat: 'png',
    browserExecutable,
    chromeMode: 'chrome-for-testing',
    timeoutInMilliseconds: 120_000,
    logLevel: 'warn',
  });
  process.stdout.write(`ATNOS_REMOTION_STILL=${JSON.stringify({ok: true, output: outputPath, frame})}\n`);
};

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  process.stderr.write(`ATNOS_REMOTION_STILL_ERROR=${message}\n`);
  process.exit(1);
});
