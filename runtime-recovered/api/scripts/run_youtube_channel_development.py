from __future__ import annotations

import argparse
import os
from pathlib import Path

from atnos.learning.youtube_channel_development import YouTubeChannelDevelopmentSession


def main() -> int:
    parser = argparse.ArgumentParser(description="Run evidence-first Atnos YouTube channel development.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("ATNOS_STATE_DIR", "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data")),
    )
    parser.add_argument("--output-root", type=Path, default=Path("E:/Atnos/LearningRuns/youtube-channel-development"))
    parser.add_argument("--semantic-cache-run", type=Path)
    args = parser.parse_args()
    session = YouTubeChannelDevelopmentSession(
        repo_root=args.repo_root,
        state_dir=args.state_dir,
        output_root=args.output_root,
        semantic_cache_run=args.semantic_cache_run,
    )
    run_dir = session.run()
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
