"""Regenerate the bundled fictional demo datasets (same as `reconcileai demo`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reconcileai.demo_data import generate, write_dataset  # noqa: E402

if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    write_dataset(generate(300, 2026, challenging=True), here / "data", here / "ground_truth.json")
    write_dataset(generate(300, 2026, challenging=False), here / "data" / "clean", here / "ground_truth_clean.json")
    print("Demo data written to", here / "data")
