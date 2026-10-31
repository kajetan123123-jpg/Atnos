from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from scripts.run_short_quality_eval import run_eval


def test_offline_short_quality_eval_uses_production_fallback() -> None:
    with TemporaryDirectory(prefix="atnos-short-eval-") as tmpdir:
        root = Path(tmpdir)
        cases_path = root / "cases.json"
        cases_path.write_text(
            json.dumps(
                [
                    {
                        "id": "local_ai",
                        "topic": "Dlaczego lokalne AI na laptopie ma sens?",
                        "expected_angle": "konkretny workflow lokalnego AI",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report = run_eval(
            settings=Settings(app_env="test", app_root=root, data_dir=root / "data"),
            cases_path=cases_path,
            live=False,
            min_score=7.0,
        )

        assert report["summary"]["passed"] == 1
        assert report["results"][0]["score"] >= 7.0
        script = report["results"][0]["script"]
        assert "przy temacie" not in script["hook"].casefold()
