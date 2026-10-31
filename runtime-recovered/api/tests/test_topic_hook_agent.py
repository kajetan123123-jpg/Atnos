from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.agents.topic_hook import HookGenerator, RawTopic, TopicHookAgent, TopicRequest, TopicScorer
from atnos.skills.router import route_task
from test_support import build_test_settings, make_temp_dir


class OfflineTopicHookAgent(TopicHookAgent):
    def _llm_topic_suggestions(self, request: TopicRequest, *, target_count: int) -> list[RawTopic]:
        return []

    def _duckduckgo_topics(self, niche: str, language: str) -> list[RawTopic]:
        return []

    def _youtube_topics(self, niche: str, language: str) -> list[RawTopic]:
        return []


class TopicHookAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_scorer_returns_four_dimension_score(self) -> None:
        scorer = TopicScorer()
        scored = scorer.score_topic(
            RawTopic(
                title="3 bledy, ktore niszcza wyniki w lokalne AI",
                source="test",
                evidence=["ai trend", "youtube shorts", "mistake"],
            ),
            niche="lokalne AI",
            language="pl",
        )

        self.assertGreaterEqual(scored.viral_score, 0.65)
        self.assertLessEqual(scored.scores.searchability, 0.25)
        self.assertLessEqual(scored.scores.shareability, 0.25)
        self.assertLessEqual(scored.scores.simplicity, 0.25)
        self.assertLessEqual(scored.scores.competition_gap, 0.25)

    def test_hook_generator_returns_three_safe_hooks(self) -> None:
        scorer = TopicScorer()
        topic = scorer.score_topic(
            RawTopic(title="Jak zrozumiec lokalne AI w 60 sekund"),
            niche="lokalne AI",
            language="pl",
        )
        hooks = HookGenerator().generate_hooks(topic, language="pl")

        self.assertGreaterEqual(len(hooks), 3)
        for hook in hooks[:3]:
            self.assertLessEqual(len(hook.text.split()), 12)
            self.assertLessEqual(len(hook.opening_line.split()), 20)

    def test_run_creates_package_history_and_logs(self) -> None:
        agent = OfflineTopicHookAgent(self.settings)
        package = agent.run(
            TopicRequest(
                channel_id="lokalne-ai",
                niche="lokalne AI i automatyzacja",
                language="pl",
                target_count=5,
            )
        )

        self.assertEqual(package.channel_id, "lokalne-ai")
        self.assertGreaterEqual(len(package.topics), 5)
        self.assertTrue(all(len(topic.hooks) >= 3 for topic in package.topics))
        self.assertTrue((self.settings.data_dir / "channels" / "lokalne-ai" / "topic_history.json").exists())
        log_text = (self.settings.data_dir / "channels" / "lokalne-ai" / "topic_hook.log").read_text(encoding="utf-8")
        self.assertIn("[TOPIC] research start", log_text)
        self.assertIn("[TOPIC] hooks generated", log_text)

    def test_approve_and_reject_update_history_and_queue(self) -> None:
        agent = OfflineTopicHookAgent(self.settings)
        package = agent.run(
            TopicRequest(
                channel_id="lokalne-ai",
                niche="lokalne AI i automatyzacja",
                language="pl",
                target_count=5,
            )
        )
        approved_id = package.topics[0].id
        rejected_id = package.topics[1].id

        approved = agent.approve(package.package_id, [approved_id])
        rejected = agent.reject(package.package_id, [rejected_id])

        self.assertEqual(next(topic for topic in approved.topics if topic.id == approved_id).status, "approved")
        self.assertEqual(next(topic for topic in rejected.topics if topic.id == rejected_id).status, "rejected")
        queue = json.loads((self.settings.data_dir / "channels" / "lokalne-ai" / "topic_queue.json").read_text(encoding="utf-8"))
        self.assertEqual(queue[0]["id"], approved_id)
        self.assertEqual(queue[0]["status"], "approved")

    def test_skill_router_selects_channel_ops_for_topic_hooks(self) -> None:
        decision = route_task("Zaproponuj 10 tematow dla kanalu lokalne-ai")

        self.assertIn("atnos:channel-ops", decision.selected_skills)


if __name__ == "__main__":
    unittest.main()
