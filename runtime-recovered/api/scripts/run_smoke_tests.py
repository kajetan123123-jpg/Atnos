from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
SMOKE_ROOT = API_ROOT / "tests" / "smoke"


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(SMOKE_ROOT),
        "-v",
        "--tb=short",
        *sys.argv[1:],
    ]
    print("Running Atnos smoke tests:")
    print(" ".join(command))
    return subprocess.call(command, cwd=str(API_ROOT), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
