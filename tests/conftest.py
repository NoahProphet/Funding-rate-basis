from pathlib import Path
from typing import Any

import pytest
import yaml

from basis_bot.config import _DecimalLoader

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = REPO_ROOT / "config" / "config.example.yaml"


@pytest.fixture
def example_data() -> dict[str, Any]:
    """The example config as a mutable dict, for building invalid variants."""
    data: dict[str, Any] = yaml.load(
        EXAMPLE_CONFIG.read_text(encoding="utf-8"), Loader=_DecimalLoader
    )
    return data
