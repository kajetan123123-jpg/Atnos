from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.github_connector_service import GitHubConnectorService

from .test_support import bootstrap_test_app, create_test_app


class GitHubConnectorServiceTest(unittest.TestCase):
    def test_read_paths_and_write_previews_work(self) -> None:
        with TemporaryDirectory(prefix="atnos-github-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                settings = app.state.settings
                database = app.state.database

                with database.session_scope() as session:
                    account = AccountCredentialsService(session, settings).create_account(
                        label="GitHub account",
                        provider_key="github",
                        account_identifier="github:owner",
                        secret_type="oauth_token_later",
                        secret_value=None,
                        risk_level="medium",
                        allowed_capabilities=[
                            "read_metadata",
                            "read_content",
                            "connector_write",
                            "confirmed_write_later",
                        ],
                        linked_integration_key="github",
                        linked_project_id=None,
                        notes=None,
                    )
                    service = GitHubConnectorService(session, settings)

                    with patch.object(
                        service,
                        "_github_get",
                        side_effect=[
                            [
                                {
                                    "name": "atnos",
                                    "private": False,
                                    "default_branch": "main",
                                    "html_url": "https://github.com/open/atnos",
                                    "owner": {"login": "open"},
                                }
                            ],
                            [
                                {
                                    "number": 12,
                                    "title": "Bug",
                                    "state": "open",
                                    "html_url": "https://github.com/open/atnos/issues/12",
                                }
                            ],
                            {
                                "path": "README.md",
                                "sha": "abc",
                                "encoding": "base64",
                                "content": "SGVsbG8=",
                            },
                        ],
                    ):
                        repos = service.list_user_repos()
                        issues = service.list_repo_issues(owner="open", repo="atnos")
                        file_content = service.read_file_content(
                            owner="open",
                            repo="atnos",
                            path="README.md",
                        )

                    issue_preview = service.create_issue_preview(
                        account_id=account.id,
                        project_id=None,
                        owner="open",
                        repo="atnos",
                        title="Bug",
                        body="Opis",
                    )
                    pr_preview = service.create_pull_request_preview(
                        account_id=account.id,
                        project_id=None,
                        owner="open",
                        repo="atnos",
                        title="Fix",
                        body="Opis",
                        head="feature",
                        base="main",
                    )
                    self.assertEqual(repos["items"][0]["name"], "atnos")
                    self.assertEqual(issues["items"][0]["number"], 12)
                    self.assertEqual(file_content["content"], "Hello")
                    self.assertEqual(issue_preview.preview_payload["action"], "create_issue")
                    self.assertEqual(pr_preview.preview_payload["action"], "create_pull_request")
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
