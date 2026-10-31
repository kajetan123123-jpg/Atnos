from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from atnos.browser_operator.logging import write_browser_action_log
from atnos.browser_operator.models import BrowserAction, BrowserObservation
from atnos.browser_operator.policies import action_requires_human_approval, classify_browser_risk
from atnos.core.config import Settings
from atnos.services.browser_runtime_service import BrowserRuntimeService


class BrowserHumanApprovalRequired(RuntimeError):
    pass


class BrowserController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.browser_runtime = BrowserRuntimeService(settings)
        self._playwright = None
        self._context = None
        self._page = None

    def start(self):
        if not self.settings.browser_enabled:
            raise RuntimeError("Browser operator is disabled in settings.")
        if self._context is not None:
            return self
        from playwright.sync_api import sync_playwright

        runtime_state = self.browser_runtime.describe()
        browser_executable = (
            self.browser_runtime.resolve_managed_browser_executable()
            or runtime_state.browser_executable
        )
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.browser_user_data_dir),
            headless=self.settings.browser_headless,
            downloads_path=str(self.settings.browser_operator_download_dir),
            executable_path=str(browser_executable) if browser_executable is not None else None,
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(self.settings.browser_default_timeout_seconds * 1000)
        return self

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
            self._page = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def goto(self, url: str):
        page = self._require_page()
        action = BrowserAction("goto", url, None, "Open the requested page.", f"Land on {url}.", "low")
        self._guard_action(action)
        page.goto(url, wait_until="domcontentloaded")
        write_browser_action_log(self.settings, url=url, action=action, result="ok")

    def observe(self) -> BrowserObservation:
        page = self._require_page()
        visible_text = self.extract_visible_text()
        clickable_elements = page.locator("button,a,[role='button']").evaluate_all(
            "(nodes) => nodes.slice(0, 20).map((node) => (node.innerText || node.getAttribute('aria-label') || node.textContent || '').trim()).filter(Boolean)"
        )
        forms = page.locator("form").evaluate_all(
            "(nodes) => nodes.map((node, index) => node.getAttribute('aria-label') || node.getAttribute('id') || `form-${index + 1}`)"
        )
        screenshot_path = self.screenshot("observation")
        return BrowserObservation(
            url=page.url,
            title=page.title(),
            visible_text=visible_text,
            clickable_elements=[str(item) for item in clickable_elements],
            forms=[str(item) for item in forms],
            screenshot_path=str(screenshot_path),
        )

    def click_by_role(self, role: str, name: str) -> None:
        page = self._require_page()
        action = BrowserAction("click", f"role={role}:{name}", None, "Click the requested role-based element.", f"Element {name} is activated.", "medium")
        self._guard_action(action)
        page.get_by_role(role, name=name).click()
        write_browser_action_log(self.settings, url=page.url, action=action, result="ok")

    def click_text(self, text: str) -> None:
        page = self._require_page()
        action = BrowserAction("click", text, None, "Click the requested visible text.", f"Text {text} is activated.", "medium")
        self._guard_action(action)
        page.get_by_text(text).click()
        write_browser_action_log(self.settings, url=page.url, action=action, result="ok")

    def fill_by_label(self, label: str, value: str) -> None:
        page = self._require_page()
        action = BrowserAction("fill_by_label", label, value, "Fill the labelled input.", f"Field {label} contains the provided value.", "medium")
        self._guard_action(action)
        page.get_by_label(label).fill(value)
        write_browser_action_log(self.settings, url=page.url, action=action, result="ok")

    def fill_by_placeholder(self, placeholder: str, value: str) -> None:
        page = self._require_page()
        action = BrowserAction("fill_by_placeholder", placeholder, value, "Fill the placeholder-based input.", f"Input {placeholder} contains the provided value.", "medium")
        self._guard_action(action)
        page.get_by_placeholder(placeholder).fill(value)
        write_browser_action_log(self.settings, url=page.url, action=action, result="ok")

    def press(self, key: str) -> None:
        page = self._require_page()
        action = BrowserAction("press", key, None, "Press the requested key.", f"Key {key} is sent to the page.", "medium")
        self._guard_action(action)
        page.keyboard.press(key)
        write_browser_action_log(self.settings, url=page.url, action=action, result="ok")

    def paste_text(self, text: str) -> None:
        page = self._require_page()
        action = BrowserAction("paste_text", "active_input", text, "Paste text into the active input.", "The active input contains the pasted text.", "medium")
        self._guard_action(action)
        page.keyboard.insert_text(text)
        write_browser_action_log(self.settings, url=page.url, action=action, result="ok")

    def upload_file(self, selector: str, path: str) -> None:
        page = self._require_page()
        action = BrowserAction("upload_file", selector, path, "Upload the selected file.", "File chooser accepts the file.", "high")
        self._guard_action(action)
        page.locator(selector).set_input_files(path)
        write_browser_action_log(self.settings, url=page.url, action=action, result="ok")

    def download_file(self, trigger_selector: str, *, suggested_name: str | None = None) -> Path:
        page = self._require_page()
        action = BrowserAction("download_file", trigger_selector, suggested_name, "Download a file from the page.", "Download exists on disk.", "medium")
        self._guard_action(action)
        with page.expect_download() as download_info:
            page.locator(trigger_selector).click()
        download = download_info.value
        target_name = suggested_name or download.suggested_filename
        target_path = (self.settings.browser_operator_download_dir / target_name).resolve()
        download.save_as(str(target_path))
        write_browser_action_log(self.settings, url=page.url, action=action, result="ok")
        return target_path

    def screenshot(self, name: str) -> Path:
        page = self._require_page()
        target = (self.settings.browser_screenshot_dir / f"{name}-{uuid4().hex}.png").resolve()
        page.screenshot(path=str(target), full_page=True)
        return target

    def extract_visible_text(self) -> str:
        page = self._require_page()
        value = page.locator("body").evaluate("(node) => node.innerText || ''")
        return str(value or "").strip()

    def _guard_action(self, action: BrowserAction) -> None:
        normalized_action = replace(action, risk_level=classify_browser_risk(action))
        if action_requires_human_approval(normalized_action, self.settings):
            raise BrowserHumanApprovalRequired(
                f"HUMAN_APPROVAL_REQUIRED: `{normalized_action.action_type}` was classified as `{normalized_action.risk_level}`."
            )

    def _require_page(self):
        if self._page is None:
            raise RuntimeError("BrowserController.start() must be called before browser actions.")
        return self._page
