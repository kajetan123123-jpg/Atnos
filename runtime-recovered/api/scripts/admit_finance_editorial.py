"""Validate or explicitly hand off an edited script to existing Atnos orders."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from atnos.core.config import get_settings
from atnos.production.editorial_revision import admit, candidate
from atnos.production.order_store import ProductionOrderStore


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--job")
    args = parser.parse_args()
    settings = get_settings()
    document = json.loads(args.source.read_text(encoding="utf-8"))
    for item in document["scripts"]:
        job = item["job_id"]
        if args.job and job != args.job:
            continue
        if args.approve:
            result = await admit(settings, job, args.source, approved=True)
        else:
            script, issues = candidate(ProductionOrderStore(settings).context(job), document)
            result = {"job_id": job, "words": sum(len(s.text.split()) for s in script.segments), "issues": issues}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
