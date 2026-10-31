"""Measure the actual built creation route with an empty browser cache, all output on E:."""
import argparse
import gzip
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--phase', required=True, choices=['before', 'after'])
args = parser.parse_args()
root = Path('E:/Atnos/App/reports/readiness-20260909/frontend-payload')
root.mkdir(parents=True, exist_ok=True)
result_path = root / (args.phase + '.json')
if result_path.exists():
    raise SystemExit('Retain the existing measurement; do not overwrite it.')
profile = root / ('profile-' + args.phase)
os.environ.update(TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp', USERPROFILE=str(profile),
                  APPDATA=str(profile / 'AppData/Roaming'), LOCALAPPDATA=str(profile / 'AppData/Local'))
from playwright.sync_api import sync_playwright

assets = Path('E:/Atnos/App/frontend/dist/assets')
result = {'scope': 'Built UI creation route with cache disabled and read-only canonical API; excludes full desktop authentication.',
          'phase': args.phase, 'page_errors': [], 'http_errors': []}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True,
        executable_path='E:/Atnos/Storage/Tools/playwright-browsers/chromium-1217/chrome-win64/chrome.exe')
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block')
    page = context.new_page()
    cdp = context.new_cdp_session(page)
    cdp.send('Network.enable')
    cdp.send('Network.setCacheDisabled', {'cacheDisabled': True})
    page.on('pageerror', lambda err: result['page_errors'].append(str(err)))
    page.on('response', lambda response: result['http_errors'].append({'url': response.url, 'status': response.status}) if response.status >= 400 else None)
    try:
        page.goto('http://127.0.0.1:3000/create')
        page.get_by_role('button', name='Bajki dla dzieci', exact=False).wait_for()
        page.wait_for_timeout(1000)
        result['resources'] = page.evaluate('performance.getEntriesByType("resource").filter(r => r.name.includes("/assets/")).map(r => ({url:r.name, bytes:r.decodedBodySize, transfer_bytes:r.transferSize, duration_ms:r.duration}))')
        result['initial_js'] = [r for r in result['resources'] if r['url'].endswith('.js')]
        result['initial_js_bytes'] = sum(r['bytes'] for r in result['initial_js'])
        result['js_files'] = [{'name': f.name, 'bytes': f.stat().st_size, 'gzip_bytes': len(gzip.compress(f.read_bytes()))} for f in sorted(assets.glob('*.js'))]
        result['total_dist_js_bytes'] = sum(r['bytes'] for r in result['js_files'])
        result['entry'] = next(r for r in result['initial_js'] if Path(r['url']).name.startswith('index-'))
        page.screenshot(path=str(root / (args.phase + '-create.png')), full_page=True)
        assert not result['page_errors']
        result['status'] = 'passed'
    except Exception as exc:
        result.update(status='failed', error=str(exc))
        raise
    finally:
        browser.close()
        result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({k: result[k] for k in ('phase', 'status', 'initial_js_bytes', 'entry', 'total_dist_js_bytes')}))
