from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reconcileai.config import Settings  # noqa: E402
from reconcileai.demo_data import generate, write_dataset  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def demo_files(tmp_path_factory):
    """Challenging demo dataset written to a temp dir (orders/bank xlsx+csv, ground truth)."""
    root = tmp_path_factory.mktemp("demo")
    ds = generate(300, 2026, challenging=True)
    write_dataset(ds, root / "data", root / "ground_truth.json")
    return root, ds


@pytest.fixture(scope="session")
def clean_files(tmp_path_factory):
    root = tmp_path_factory.mktemp("clean")
    ds = generate(300, 2026, challenging=False)
    write_dataset(ds, root / "data" / "clean", root / "ground_truth_clean.json")
    return root, ds
