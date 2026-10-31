"""Exercise the official deployment against an isolated, disposable E: repo."""
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows desktop launcher contract')
def test_deploy_retains_chunks_needed_by_already_open_tabs(tmp_path):
    repo = tmp_path / 'deployment-repo'
    dist = repo / 'frontend/dist'; target = repo / 'desktop-web'
    (dist / 'assets').mkdir(parents=True)
    (target / 'assets').mkdir(parents=True)
    (target / 'assets/Chat-old.js').write_text('old session code', encoding='utf-8')
    (target / 'index.html').write_text('<script src="/assets/Chat-old.js"></script>', encoding='utf-8')
    (dist / 'assets/Chat-new.js').write_text('new session code', encoding='utf-8')
    new_index = '<script src="/assets/Chat-new.js"></script>'
    (dist / 'index.html').write_text(new_index, encoding='utf-8')
    script = Path(__file__).resolve().parents[3] / 'scripts/deploy_frontend_to_desktop_web.ps1'
    result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
                             '-RepoRoot',str(repo)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (target / 'assets/Chat-old.js').is_file(), 'Open tabs still reference this immutable chunk'
    assert (target / 'assets/Chat-old.js').read_text() == 'old session code'
    assert (target / 'assets/Chat-new.js').read_text() == 'new session code'
    assert (target / 'index.html').read_text() == new_index
    assert not list(target.glob('.index.deploy-*.html'))
