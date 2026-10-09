from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from basis_bot.config import ConfigError, load_config, parse_config
from tests.conftest import EXAMPLE_CONFIG

DUMMY = Path("test.yaml")


def _errors(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ConfigError) as info:
        parse_config(data, DUMMY)
    return info.value.errors


def test_example_config_loads() -> None:
    config = load_config(EXAMPLE_CONFIG)
    assert config.venue.symbols == ["BTC", "ETH"]
    assert config.alerts.daily_summary_utc == time(6, 0)
    assert config.alerts.quiet_hours_utc is None


def test_yaml_floats_are_exact_decimals() -> None:
    config = load_config(EXAMPLE_CONFIG)
    assert config.strategy.entry_apr == Decimal("0.15")
    assert config.reconcile.qty_tolerance_ratio == Decimal("0.001")
    assert isinstance(config.risk.max_daily_loss_pct_nav, Decimal)


def test_config_hash_is_stable_and_content_sensitive(
    example_data: dict[str, Any],
) -> None:
    first = parse_config(example_data, DUMMY).config_hash()
    assert parse_config(example_data, DUMMY).config_hash() == first
    example_data["risk"]["max_net_delta_usd"] = 51
    assert parse_config(example_data, DUMMY).config_hash() != first


def test_unknown_key_is_rejected(example_data: dict[str, Any]) -> None:
    example_data["risk"]["max_net_delta"] = 50
    assert any("risk.max_net_delta" in e for e in _errors(example_data))


def test_missing_section_is_rejected(example_data: dict[str, Any]) -> None:
    del example_data["risk"]
    assert any(e.startswith("risk:") for e in _errors(example_data))


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("risk", "max_perp_leverage", 0),
        ("risk", "max_total_notional_usd", -1),
        ("risk", "min_liq_distance_pct", 100),
        ("risk", "max_orders_per_minute", 0),
        ("execution", "max_slippage_bps", 0),
        ("strategy", "ema_periods", 0),
        ("reconcile", "qty_tolerance_ratio", 1),
    ],
)
def test_out_of_range_values_are_rejected(
    example_data: dict[str, Any], section: str, key: str, value: int
) -> None:
    example_data[section][key] = value
    assert any(f"{section}.{key}" in e for e in _errors(example_data))


def test_nan_is_rejected(example_data: dict[str, Any]) -> None:
    example_data["risk"]["max_net_delta_usd"] = Decimal("NaN")
    assert any("max_net_delta_usd" in e for e in _errors(example_data))


@pytest.mark.parametrize(
    ("section", "changes", "message"),
    [
        ("strategy", {"entry_apr": Decimal("0.05")}, "entry_apr"),
        ("risk", {"warn_liq_distance_pct": 40}, "warn_liq_distance_pct"),
        ("risk", {"max_symbol_notional_usd": 10}, "max_symbol_notional"),
        ("execution", {"max_hedge_slippage_bps": 4}, "max_hedge_slippage"),
    ],
)
def test_section_cross_checks(
    example_data: dict[str, Any],
    section: str,
    changes: dict[str, Any],
    message: str,
) -> None:
    example_data[section].update(changes)
    assert any(message in e for e in _errors(example_data))


def test_targets_must_match_symbols(example_data: dict[str, Any]) -> None:
    del example_data["strategy"]["target_notional_usd"]["ETH"]
    assert any("must match venue.symbols" in e for e in _errors(example_data))


def test_target_above_symbol_limit(example_data: dict[str, Any]) -> None:
    example_data["strategy"]["target_notional_usd"]["BTC"] = 100
    assert any("above max_symbol" in e for e in _errors(example_data))


def test_target_sum_above_total_limit(example_data: dict[str, Any]) -> None:
    example_data["risk"]["max_total_notional_usd"] = 150
    example_data["risk"]["max_symbol_notional_usd"] = 100
    example_data["strategy"]["target_notional_usd"] = {"BTC": 100, "ETH": 100}
    assert any("sum of target" in e for e in _errors(example_data))


def test_chunk_above_single_order_limit(example_data: dict[str, Any]) -> None:
    example_data["execution"]["max_chunk_notional_usd"] = 10
    assert any("max_chunk_notional_usd" in e for e in _errors(example_data))


def test_topup_trigger_below_min_liq(example_data: dict[str, Any]) -> None:
    example_data["margin"]["topup_trigger_pct"] = 40
    assert any("topup_trigger_pct" in e for e in _errors(example_data))


@pytest.mark.parametrize("symbols", [[], ["btc"], ["BTC", "BTC"]])
def test_bad_symbols(example_data: dict[str, Any], symbols: list[str]) -> None:
    example_data["venue"]["symbols"] = symbols
    assert any("venue.symbols" in e for e in _errors(example_data))


def test_unquoted_time_is_rejected(example_data: dict[str, Any]) -> None:
    # What PyYAML produces for an unquoted 22:00.
    example_data["alerts"]["daily_summary_utc"] = 1320
    assert any("quoted" in e for e in _errors(example_data))


def test_quiet_hours_parse(example_data: dict[str, Any]) -> None:
    example_data["alerts"]["quiet_hours_utc"] = {
        "start": "22:00",
        "end": "06:00",
    }
    quiet = parse_config(example_data, DUMMY).alerts.quiet_hours_utc
    assert quiet is not None
    assert (quiet.start, quiet.end) == (time(22), time(6))


def test_invalid_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("risk: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_non_finite_yaml_float(tmp_path: Path) -> None:
    path = tmp_path / "inf.yaml"
    path.write_text("risk:\n  max_net_delta_usd: .inf\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a finite decimal"):
        load_config(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read file"):
        load_config(tmp_path / "nope.yaml")
