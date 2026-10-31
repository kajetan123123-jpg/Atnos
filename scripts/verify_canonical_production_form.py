"""Exercise the shipped creation form against the real local backend, all artifacts on E:."""
from pathlib import Path
import argparse
import json
import os
import re
from urllib.parse import urlsplit

ROOT = Path('E:/Atnos/App/reports/readiness-20260909/canonical-form-20260914')
ROOT.mkdir(parents=True, exist_ok=True)
profile = ROOT / 'profile-home'
os.environ.update(USERPROFILE=str(profile), APPDATA=str(profile/'AppData/Roaming'),
                  LOCALAPPDATA=str(profile/'AppData/Local'), TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--create', action='store_true')
args = parser.parse_args()
state_file = ROOT / 'submission.json'
submission = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
TOPIC = 'Build a weekly savings habit with a clear payday routine'
DIRECTION = 'English short for adults. Set a modest weekly transfer after payday, keep it affordable, and check actual progress. If using an example: ten dollars each week for fifty-two weeks is five hundred twenty dollars, ignoring interest. No promised returns, no invented review intervals.'
NOTES = 'Show weekly scheduling clearly in a diagram. Use visuals that directly match the narration. Avoid unrelated travel documents, a calculator for scheduling a transfer, and calendar images when the narration is about choosing an affordable amount.'
report = {'scope': 'Shipped UI on port 3000 and full canonical Atnos API on port 8000. Separate test browser profile on E; no mocked responses. No publishing.',
          'page_errors': [], 'http_errors': [], 'ui_endpoints': {}, 'checks': []}
with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(str(ROOT/'browser-profile'),
        executable_path='E:/Atnos/Storage/Tools/playwright-browsers/chromium-1217/chrome-win64/chrome.exe',
        headless=True, viewport={'width': 1440, 'height': 1000})
    page = context.pages[0]
    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
    def response_seen(response):
        path = urlsplit(response.url).path
        if path in ('/ui/bootstrap', '/ui/shell'):
            report['ui_endpoints'][path] = response.status
        if response.status >= 400:
            report['http_errors'].append({'path': path, 'status': response.status})
    page.on('response', response_seen)
    try:
        ready = context.request.get('http://127.0.0.1:8000/health/ready')
        assert ready.status == 200
        page.goto('http://127.0.0.1:3000/create')
        page.get_by_role('heading', name='Od pomysłu do gotowego filmu.').wait_for(timeout=30000)
        page.get_by_role('button', name=re.compile('Wealth Blueprint')).wait_for(timeout=30000)
        page.get_by_label('Temat lub pomysł').fill(TOPIC)
        page.get_by_label('Co widz ma z tego wynieść?').fill(DIRECTION)
        page.get_by_text('Dodatkowe wskazówki', exact=True).click()
        page.get_by_label('Uwagi do obrazu, narracji lub montażu').fill(NOTES)
        assert page.get_by_role('button', name='Utwórz rolkę', exact=True).is_enabled()
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000})
            assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
            page.screenshot(path=str(ROOT/f'form-{width}.png'), full_page=True)
            report['checks'].append({'form_width': width, 'overflow': False})
        assert report['ui_endpoints'].get('/ui/bootstrap') == 200, report['ui_endpoints']
        assert report['ui_endpoints'].get('/ui/shell') == 200, report['ui_endpoints']
        if args.create and not submission.get('job_id'):
            if submission.get('attempted'):
                orders = context.request.get('http://127.0.0.1:8000/api/production/orders?limit=100').json()['orders']
                matches = [o for o in orders if o['topic_title'] == TOPIC]
                assert len(matches) == 1, 'Uncertain earlier submission: inspect before submitting again'
                submission['job_id'] = matches[0]['id']
                state_file.write_text(json.dumps(submission, indent=2), encoding='utf-8')
            else:
                submission = {'attempted': True, 'topic': TOPIC, 'delivery_mode': 'local_only'}
                state_file.write_text(json.dumps(submission, indent=2), encoding='utf-8')
                with page.expect_response(lambda r: urlsplit(r.url).path == '/api/production/orders' and r.request.method == 'POST', timeout=30000) as response:
                    page.get_by_role('button', name='Utwórz rolkę', exact=True).click()
                accepted = response.value
                assert accepted.status == 202, f'Submission returned HTTP {accepted.status}'
                payload = accepted.json()
                submission.update(job_id=payload['id'], launch_pid=payload.get('launch_pid'), response_status=accepted.status)
                state_file.write_text(json.dumps(submission, indent=2), encoding='utf-8')
                page.wait_for_url('**/studio/'+payload['id'])
        if submission.get('job_id'):
            job_id = submission['job_id']
            page.goto('http://127.0.0.1:3000/studio/'+job_id)
            page.get_by_text(TOPIC, exact=True).first.wait_for(timeout=30000)
            job = context.request.get('http://127.0.0.1:8000/api/production/orders/'+job_id).json()
            (ROOT/'current-order.json').write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding='utf-8')
            report['order'] = {k: job.get(k) for k in ('id','status','current_step','provider_calls','max_provider_calls','budget_limit_usd','reserved_cost_units','worker_live')}
            page.screenshot(path=str(ROOT/'studio-current.png'), full_page=True)
        assert not report['page_errors'], report['page_errors']
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        page.screenshot(path=str(ROOT/'failure.png'), full_page=True)
        raise
    finally:
        (ROOT/'inspection.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        context.close()
print(json.dumps(report, ensure_ascii=False))
