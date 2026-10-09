from pathlib import Path

import pytest

from basis_bot.core.cli import EXIT_BAD_CONFIG, EXIT_OK, check_config, main
from tests.conftest import EXAMPLE_CONFIG


def test_check_config_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_config(EXAMPLE_CONFIG) == EXIT_OK
    assert "config OK" in capsys.readouterr().out

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "max_perp_leverage: 2", "max_perp_leverage: -2"
        ),
        encoding="utf-8",
    )
    assert check_config(bad) == EXIT_BAD_CONFIG
    err = capsys.readouterr().err
    assert "config INVALID" in err
    assert "risk.max_perp_leverage: Input should be greater than 0" in err


def test_missing_config_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BASIS_BOT_CONFIG", raising=False)
    assert main(["check-config"]) == EXIT_BAD_CONFIG
    assert "no config" in capsys.readouterr().err
