from __future__ import annotations

import asyncio

from atnos.core.config import Settings
from atnos.production.voice_provider_doctor import write_voice_provider_doctor_report


async def _main() -> None:
    settings = Settings()
    report = await write_voice_provider_doctor_report(settings)
    print(f"Public voice ready: {report['public_voice_ready']}")
    print(f"Next action: {report['next_action']}")
    print(f"JSON report: {report['report_path']}")
    print(f"Markdown report: {report['markdown_report_path']}")


if __name__ == "__main__":
    asyncio.run(_main())
