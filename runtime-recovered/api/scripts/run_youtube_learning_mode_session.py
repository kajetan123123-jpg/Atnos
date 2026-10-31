from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings, load_runtime_env_files  # noqa: E402
from atnos.learning.youtube_session import YouTubeLearningSession  # noqa: E402


SAFE_ENV = {
    "LEARNING_MODE": "true",
    "YOUTUBE_WRITE_BLOCK": "true",
    "YOUTUBE_UPLOAD_ENABLED": "false",
    "YOUTUBE_EDIT_ENABLED": "false",
    "YOUTUBE_DELETE_ENABLED": "false",
    "YOUTUBE_COMMENT_ENABLED": "false",
    "YOUTUBE_PLAYLIST_WRITE_ENABLED": "false",
    "YOUTUBE_SCOPE_ESCALATION_ALLOWED": "false",
    "EXTERNAL_AI_TRANSFER_ALLOWED": "false",
    "CLOUD_LLM_ALLOWED": "false",
    "LOCAL_PROCESSING_ONLY": "true",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Atnos local YouTube metadata learning session.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path(os.environ.get("OBSIDIAN_VAULT_PATH", "E:/Atnos/Knowledge/Obsidian/Atnos-Brain")),
    )
    parser.add_argument("--output-base", type=Path, default=Path("E:/Atnos/LearningRuns"))
    parser.add_argument("--max-hours", type=float, default=8.0)
    parser.add_argument("--metadata-per-channel", type=int, default=30)
    parser.add_argument("--deep-per-channel", type=int, default=5)
    args = parser.parse_args()

    for key, value in SAFE_ENV.items():
        os.environ[key] = value
    os.environ.setdefault("TEMP", "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/temp")
    os.environ.setdefault("TMP", "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/temp")
    Path(os.environ["TEMP"]).mkdir(parents=True, exist_ok=True)

    load_runtime_env_files()
    settings = Settings()
    session = YouTubeLearningSession(
        settings,
        repo_path=args.repo,
        vault_path=args.vault,
        output_base=args.output_base,
        max_hours=args.max_hours,
        metadata_per_channel=args.metadata_per_channel,
        deep_per_channel=args.deep_per_channel,
    )
    result = asyncio.run(session.run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
