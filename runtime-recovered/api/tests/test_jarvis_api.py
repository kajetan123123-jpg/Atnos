from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from .test_support import create_test_app, make_temp_dir


class _FakeJarvisController:
    def __init__(self) -> None:
        self.goal: str | None = None
        self.manual_login_service: str | None = None
        self.started_manual_login_service: str | None = None
        self.started_manual_login_for_url_payload: dict[str, str | None] | None = None
        self.running = False
        self.sessions = [
            {
                "service": "ideogram",
                "display_name": "Ideogram",
                "login_url": "https://ideogram.ai/login",
                "validation_url": "https://ideogram.ai/t/explore",
                "storage_state_path": "F:/tmp/ideogram.json",
                "ready": False,
                "expired": False,
                "pending_manual_login": False,
                "has_storage_state": False,
                "login_window_open": False,
                "status_reason": "Brak zapisanej sesji browserowej.",
                "semaphore_free": 5,
            }
        ]

    async def start(self):
        self.running = True
        return self.status()

    async def stop(self):
        self.running = False
        return self.status()

    def status(self):
        return {
            "running": self.running,
            "config": {"config_path": "F:/tmp/jarvis/config.json"},
            "modules": {"worker_pool": {"running": self.running}},
        }

    async def inject_goal(self, goal: str) -> None:
        self.goal = goal

    async def list_browser_sessions(self):
        return self.sessions

    async def start_manual_login(self, service: str):
        self.started_manual_login_service = service
        self.sessions[0] = {
            **self.sessions[0],
            "pending_manual_login": True,
            "login_window_open": True,
            "status_reason": "Okno logowania jest otwarte. Dokoncz logowanie i kliknij Potwierdz zapis.",
        }
        return True, self.sessions[0]

    async def start_manual_login_for_url(
        self,
        *,
        service: str,
        display_name: str,
        login_url: str,
        validation_url: str | None = None,
    ):
        self.started_manual_login_for_url_payload = {
            "service": service,
            "display_name": display_name,
            "login_url": login_url,
            "validation_url": validation_url,
        }
        self.sessions[0] = {
            **self.sessions[0],
            "service": service,
            "display_name": display_name,
            "login_url": login_url,
            "validation_url": validation_url or login_url,
            "pending_manual_login": True,
            "login_window_open": True,
            "status_reason": "Okno logowania jest otwarte. Dokoncz logowanie i kliknij Potwierdz zapis.",
        }
        return True, self.sessions[0]

    async def confirm_manual_login(self, service: str):
        self.manual_login_service = service
        self.sessions[0] = {
            **self.sessions[0],
            "ready": True,
            "pending_manual_login": False,
            "login_window_open": False,
            "has_storage_state": True,
            "status_reason": "Gotowa zapisana sesja browserowa.",
        }
        return True, self.sessions[0]


class TestJarvisApi:
    def test_jarvis_control_surface_routes(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            app.state.jarvis_controller = _FakeJarvisController()

            with TestClient(app) as client:
                status_response = client.get("/jarvis/status")
                assert status_response.status_code == 200
                assert status_response.json()["running"] is False

                start_response = client.post("/jarvis/start")
                assert start_response.status_code == 200
                assert start_response.json()["status"]["running"] is True

                goal_response = client.post("/jarvis/goals", json={"goal": "Reach 10k subs"})
                assert goal_response.status_code == 200
                assert app.state.jarvis_controller.goal == "Reach 10k subs"

                sessions_response = client.get("/jarvis/browser-sessions")
                assert sessions_response.status_code == 200
                assert sessions_response.json()["sessions"][0]["service"] == "ideogram"

                start_login_response = client.post("/jarvis/browser-sessions/ideogram/manual-login/start")
                assert start_login_response.status_code == 200
                assert start_login_response.json()["ok"] is True
                assert app.state.jarvis_controller.started_manual_login_service == "ideogram"
                assert start_login_response.json()["session"]["login_window_open"] is True

                start_url_login_response = client.post(
                    "/jarvis/browser-sessions/manual-login/start",
                    json={
                        "service": "claude",
                        "display_name": "Claude",
                        "login_url": "https://claude.ai/login",
                        "validation_url": "https://claude.ai/new",
                    },
                )
                assert start_url_login_response.status_code == 200
                assert start_url_login_response.json()["ok"] is True
                assert app.state.jarvis_controller.started_manual_login_for_url_payload == {
                    "service": "claude",
                    "display_name": "Claude",
                    "login_url": "https://claude.ai/login",
                    "validation_url": "https://claude.ai/new",
                }

                confirm_browser_login_response = client.post(
                    "/jarvis/browser-sessions/ideogram/manual-login/confirm"
                )
                assert confirm_browser_login_response.status_code == 200
                assert confirm_browser_login_response.json()["ok"] is True
                assert confirm_browser_login_response.json()["session"]["ready"] is True

                login_response = client.post("/jarvis/manual-login-confirmed", json={"service": "capcut"})
                assert login_response.status_code == 200
                assert app.state.jarvis_controller.manual_login_service == "capcut"

                stop_response = client.post("/jarvis/stop")
                assert stop_response.status_code == 200
                assert stop_response.json()["status"]["running"] is False
