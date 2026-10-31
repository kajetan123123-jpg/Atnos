from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atnos.core.config import Settings
from content.script_generator import ScriptGenerator
from content.short_format_strategy import build_short_strategy_brief, choose_short_format


class ShortFormatStrategyTest(unittest.TestCase):
    def test_tool_stack_topic_selects_tool_stack_strategy(self) -> None:
        strategy = choose_short_format("Submagic, Klap i CapCut - do czego realnie uzyc kazdego narzedzia")

        self.assertEqual(strategy.key, "tool_stack")
        self.assertIn("workflow map", strategy.retention_devices)
        self.assertIn("do not invent tool features", strategy.risk_notes)

    def test_mistake_topic_selects_mistake_warning_strategy(self) -> None:
        strategy = choose_short_format("3 bledy przez ktore faceless kanal wyglada jak spam")

        self.assertEqual(strategy.key, "mistake_warning")
        self.assertTrue(any("blad" in item.casefold() for item in strategy.hook_patterns))

    def test_strategy_brief_contains_retention_timeline_and_visual_direction(self) -> None:
        brief = build_short_strategy_brief("jak zrobic shorta AI z lokalnym workflow")
        prompt_section = brief.to_prompt_section()

        self.assertIn("SHORT CREATIVE BRIEF", prompt_section)
        self.assertIn("SHORT FORMAT STRATEGY", prompt_section)
        self.assertIn("beat timeline", prompt_section)
        self.assertIn("retention devices", prompt_section)
        self.assertIn("visual direction", prompt_section)
        self.assertIn("first 2 seconds", prompt_section)

    def test_script_generator_brief_includes_format_strategy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(app_root=root, data_dir=root / "data")
            brief = ScriptGenerator(settings).build_brief("porownaj Ollama i Claude do shortow")

        self.assertIn("SHORT FORMAT STRATEGY", brief)
        self.assertIn("Comparison", brief)
        self.assertIn("caption style", brief)
        self.assertIn("viewer promise", brief)


if __name__ == "__main__":
    unittest.main()
