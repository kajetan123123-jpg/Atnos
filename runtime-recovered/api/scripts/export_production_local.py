"""Verify and export selected completed orders. No provider calls or publication."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atnos.core.config import get_settings
from atnos.production.local_export import export_batch
from atnos.production.order_store import ProductionOrderStore


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--job", action="append", required=True, metavar="ID=FOLDER")
    args = parser.parse_args()
    jobs = [tuple(value.split("=", 1)) for value in args.job]
    if any(len(pair) != 2 for pair in jobs):
        parser.error("Each --job must have the form ID=FOLDER")
    print(json.dumps(export_batch(ProductionOrderStore(get_settings()), jobs, args.destination), indent=2))
