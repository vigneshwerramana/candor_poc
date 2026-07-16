import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from candor_qa.config import load_config  # noqa: E402


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def config():
    return load_config(ROOT / "config")


@pytest.fixture(scope="session")
def sample_data(root) -> Path:
    """Ensure the deterministic synthetic snapshots + labels exist."""
    data = root / "data"
    if not (data / "providers_v1.csv").exists():
        subprocess.run(
            [sys.executable, str(root / "scripts" / "generate_sample_data.py")],
            check=True,
        )
    return data
