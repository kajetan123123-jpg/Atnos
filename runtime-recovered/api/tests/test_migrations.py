from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.db.migrations import upgrade_database


class MigrationsTest(unittest.TestCase):
    def test_migrations_create_remote_worker_tables_and_creator_strategy_profile(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        settings = build_test_settings(root)

        upgrade_database(settings)

        connection = sqlite3.connect(settings.sqlite_file)
        self.addCleanup(connection.close)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertIn("scheduled_tasks", tables)
        self.assertIn("messages", tables)
        self.assertIn("remote_nodes", tables)
        self.assertIn("remote_execution_runs", tables)
        self.assertIn("trend_watch_snapshots", tables)
        self.assertIn("operational_work_items", tables)

        message_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(messages)")
        }
        self.assertIn("metadata_json", message_columns)

        creator_project_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(creator_projects)")
        }
        self.assertIn("strategy_profile_json", creator_project_columns)

        remote_node_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(remote_nodes)")
        }
        self.assertIn("display_mode", remote_node_columns)
        self.assertIn("browser_default_target", remote_node_columns)

        runtime_control_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(runtime_control_settings)")
        }
        self.assertIn("trend_watch_enabled", runtime_control_columns)
        self.assertIn("trend_watch_query", runtime_control_columns)

        trend_watch_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(trend_watch_snapshots)")
        }
        self.assertIn("topic_query", trend_watch_columns)
        self.assertIn("summary_json", trend_watch_columns)

        operational_memory_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(operational_work_items)")
        }
        self.assertIn("review_state", operational_memory_columns)
        self.assertIn("blocked_reason", operational_memory_columns)

        project_app_link_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(project_app_links)")
        }
        self.assertTrue(
            {
                "resource_type",
                "resource_external_id",
                "resource_label",
                "binding_role",
                "environment",
                "confirmation_policy",
                "delegation_policy",
                "allow_shared_account",
                "permission_scopes_json",
                "connection_status",
                "connection_detail",
                "last_verified_at",
                "verification_evidence_json",
            }.issubset(project_app_link_columns)
        )
        project_app_link_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(project_app_links)")
        }
        self.assertIn("ix_project_app_links_resource_external_id", project_app_link_indexes)
        self.assertIn("ix_project_app_links_connection_status", project_app_link_indexes)

    def test_migrations_normalize_legacy_jarvis_address_values(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        settings = build_test_settings(root)

        upgrade_database(settings, revision="20260510_0030")

        connection = sqlite3.connect(settings.sqlite_file)
        self.addCleanup(connection.close)
        connection.execute(
            """
            INSERT INTO users (
                created_at,
                updated_at,
                display_name,
                handle,
                locale,
                timezone,
                is_owner
            ) VALUES (
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP,
                'Owner',
                'owner',
                'pl-PL',
                'Europe/Warsaw',
                1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO execution_policies (
                created_at,
                updated_at,
                user_id,
                automation_level,
                verification_mode,
                allow_local_file_read,
                allow_local_file_write,
                allow_web_read,
                allow_external_fallback,
                allow_external_attachment_metadata,
                allow_external_attachment_content,
                prefer_local_llm,
                default_provider_posture,
                default_persona_profile,
                jarvis_address_as
            ) VALUES (
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP,
                1,
                'ASSISTED',
                'SMART',
                1,
                0,
                1,
                1,
                1,
                0,
                1,
                'PREFER_LOCAL',
                'STRATEGIST',
                'none'
            )
            """
        )
        connection.commit()
        connection.close()

        upgrade_database(settings)

        verification = sqlite3.connect(settings.sqlite_file)
        self.addCleanup(verification.close)
        normalized_value = verification.execute(
            "SELECT jarvis_address_as FROM execution_policies WHERE user_id = 1"
        ).fetchone()
        self.assertEqual(("NONE",), normalized_value)
