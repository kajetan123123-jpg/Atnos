from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from test_support import create_test_app


def test_real_action_api_surface_exposes_mail_youtube_and_preflight_routes(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    paths = {route.path for route in app.routes}

    required_paths = {
        "/preflight/shorts-real-actions",
        "/connectors/smtp-mail/status",
        "/connectors/smtp-mail/probe",
        "/connectors/smtp-mail/send-test",
        "/connectors/gmail/status",
        "/connectors/gmail/probe",
        "/connectors/gmail/drafts",
        "/connectors/youtube/status",
        "/connectors/youtube/probe",
        "/oauth/youtube-studio/import-client-secret",
        "/oauth/youtube-studio/client-secret-status",
        "/oauth/youtube-studio/connect",
        "/oauth/{connector_key}/start",
        "/api/production/readiness",
        "/api/production/preflight",
        "/api/production/start",
    }

    assert required_paths <= paths
