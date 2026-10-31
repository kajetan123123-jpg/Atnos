from __future__ import annotations

import json
from pathlib import Path
import sys

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import get_settings  # noqa: E402
from atnos.intelligence.storage import read_intelligence_account_config, upsert_intelligence_account_config  # noqa: E402


PROFILE_PATH = API_ROOT / "config" / "channel_profiles" / "wealth_blueprint.json"


def main() -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    account_id = str(profile["account_id"])
    niches = [
        str(profile["niche"]),
        *[str(item) for item in profile.get("sub_niches", [])],
        *[str(item) for item in profile.get("allowed_topics", [])[:8]],
    ]
    content_profile = (
        "Wealth Blueprint | English personal finance Shorts | US/global | "
        "dark minimalist fintech style | no investment advice, no stock picks, no get-rich-quick claims"
    )
    settings = get_settings()
    settings.ensure_directories()
    upsert_intelligence_account_config(
        settings,
        account_id=account_id,
        niches=niches,
        region=str(profile.get("region") or "US"),
        timeframe="now 7-d",
        youtube_oauth_account_id=None,
        gmail_oauth_account_id=None,
        channel_id=str(profile.get("channel_id") or "").strip() or None,
        channel_title=str(profile.get("channel_name") or "Wealth Blueprint"),
        content_profile=content_profile,
    )
    stored = read_intelligence_account_config(settings, account_id=account_id)
    print(json.dumps(stored, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
