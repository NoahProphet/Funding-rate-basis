"""Bot configuration: YAML file validated into frozen pydantic models."""

import hashlib
import json
import re
from datetime import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

NonNegUsd = Annotated[Decimal, Field(ge=0)]
PosDecimal = Annotated[Decimal, Field(gt=0)]
Pct = Annotated[Decimal, Field(gt=0, lt=100)]
Bps = Annotated[Decimal, Field(gt=0, le=10_000)]
PosInt = Annotated[int, Field(gt=0)]

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")


class ConfigError(Exception):
    """Config file is unreadable or invalid; `errors` is human-readable."""

    def __init__(self, path: Path, errors: list[str]) -> None:
        self.path = path
        self.errors = errors
        super().__init__(f"invalid config {path}: " + "; ".join(errors))


class _Model(BaseModel):
    # Unknown keys are almost always typos of a real limit; reject them.
    model_config = ConfigDict(extra="forbid", frozen=True)


class VenueConfig(_Model):
    name: str = Field(min_length=1)
    symbols: list[str] = Field(min_length=1)

    @field_validator("symbols")
    @classmethod
    def _check_symbols(cls, symbols: list[str]) -> list[str]:
        bad = [s for s in symbols if not _SYMBOL_RE.match(s)]
        if bad:
            raise ValueError(f"symbols must be uppercase base assets: {bad}")
        if len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be unique")
        return symbols


class StrategyConfig(_Model):
    entry_apr: Decimal
    exit_apr: Decimal
    entry_confirm_periods: PosInt
    exit_confirm_periods: PosInt
    ema_periods: PosInt
    expected_hold_days: PosDecimal
    target_notional_usd: dict[str, NonNegUsd]

    @model_validator(mode="after")
    def _check_hysteresis(self) -> Self:
        if self.entry_apr <= self.exit_apr:
            raise ValueError("entry_apr must be greater than exit_apr")
        return self


class RiskConfig(_Model):
    max_total_notional_usd: NonNegUsd
    max_symbol_notional_usd: NonNegUsd
    max_perp_leverage: PosDecimal
    min_liq_distance_pct: Pct
    warn_liq_distance_pct: Pct
    max_net_delta_usd: NonNegUsd
    max_daily_loss_pct_nav: Pct
    max_single_order_usd: NonNegUsd
    max_orders_per_minute: PosInt
    max_data_staleness_s: PosDecimal
    max_clock_drift_ms: PosInt
    max_consecutive_api_errors: PosInt

    @model_validator(mode="after")
    def _check_limits(self) -> Self:
        if self.max_symbol_notional_usd > self.max_total_notional_usd:
            raise ValueError(
                "max_symbol_notional_usd exceeds max_total_notional_usd"
            )
        if self.warn_liq_distance_pct <= self.min_liq_distance_pct:
            raise ValueError(
                "warn_liq_distance_pct must be greater than "
                "min_liq_distance_pct"
            )
        return self


class ExecutionConfig(_Model):
    max_chunk_notional_usd: NonNegUsd
    max_slippage_bps: Bps
    max_hedge_slippage_bps: Bps
    hedge_timeout_s: PosDecimal

    @model_validator(mode="after")
    def _check_slippage(self) -> Self:
        if self.max_hedge_slippage_bps < self.max_slippage_bps:
            raise ValueError(
                "max_hedge_slippage_bps must be >= max_slippage_bps"
            )
        return self


class ReconcileConfig(_Model):
    interval_s: PosDecimal
    qty_tolerance_ratio: Annotated[Decimal, Field(ge=0, lt=1)]


class ControlConfig(_Model):
    kill_flattens: bool


class MarginConfig(_Model):
    topup_trigger_pct: Pct
    max_transfer_usd_per_hour: NonNegUsd


def _parse_utc_time(value: object) -> object:
    # Unquoted YAML 1.1 values like 22:00 load as base-60 ints (1320),
    # which pydantic would silently read as seconds.
    if not isinstance(value, str):
        raise ValueError('must be a quoted "HH:MM" string')
    return value


class QuietHours(_Model):
    start: time
    end: time

    @field_validator("start", "end", mode="before")
    @classmethod
    def _check_times(cls, value: object) -> object:
        return _parse_utc_time(value)


class AlertsConfig(_Model):
    dedup_window_s: PosInt
    warn_escalate_after_s: PosInt
    daily_summary_utc: time
    quiet_hours_utc: QuietHours | None

    @field_validator("daily_summary_utc", mode="before")
    @classmethod
    def _check_summary_time(cls, value: object) -> object:
        return _parse_utc_time(value)


class WatchdogConfig(_Model):
    timeout_s: PosInt


class DatabaseConfig(_Model):
    host: str = Field(min_length=1)
    port: Annotated[int, Field(gt=0, lt=65536)]
    name: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password_file: Path


class LoggingConfig(_Model):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Config(_Model):
    venue: VenueConfig
    strategy: StrategyConfig
    risk: RiskConfig
    execution: ExecutionConfig
    reconcile: ReconcileConfig
    control: ControlConfig
    margin: MarginConfig
    alerts: AlertsConfig
    watchdog: WatchdogConfig
    database: DatabaseConfig
    logging: LoggingConfig

    @model_validator(mode="after")
    def _check_targets(self) -> Self:
        targets = self.strategy.target_notional_usd
        if set(targets) != set(self.venue.symbols):
            raise ValueError(
                "strategy.target_notional_usd keys must match "
                f"venue.symbols {self.venue.symbols}, got {sorted(targets)}"
            )
        too_big = [
            s
            for s, usd in targets.items()
            if usd > self.risk.max_symbol_notional_usd
        ]
        if too_big:
            raise ValueError(
                f"target_notional_usd above max_symbol_notional_usd: {too_big}"
            )
        if sum(targets.values()) > self.risk.max_total_notional_usd:
            raise ValueError(
                "sum of target_notional_usd exceeds max_total_notional_usd"
            )
        return self

    @model_validator(mode="after")
    def _check_cross_section(self) -> Self:
        exe, risk = self.execution, self.risk
        if exe.max_chunk_notional_usd > risk.max_single_order_usd:
            raise ValueError(
                "execution.max_chunk_notional_usd exceeds "
                "risk.max_single_order_usd"
            )
        if self.margin.topup_trigger_pct <= risk.min_liq_distance_pct:
            raise ValueError(
                "margin.topup_trigger_pct must be greater than "
                "risk.min_liq_distance_pct"
            )
        return self

    def config_hash(self) -> str:
        return hashlib.sha256(canonical_json(self).encode()).hexdigest()


def canonical_json(config: Config) -> str:
    return json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )


class _DecimalLoader(yaml.SafeLoader):
    """SafeLoader that parses YAML floats as Decimal, never float."""


def _construct_decimal(loader: yaml.SafeLoader, node: yaml.Node) -> Decimal:
    raw = str(loader.construct_scalar(node))  # type: ignore[arg-type]
    try:
        value = Decimal(raw.replace("_", ""))
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite():
        raise yaml.constructor.ConstructorError(
            None, None, f"not a finite decimal: {raw!r}", node.start_mark
        )
    return value


_DecimalLoader.add_constructor("tag:yaml.org,2002:float", _construct_decimal)


def parse_config(data: Any, path: Path) -> Config:
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(path, _format_errors(exc)) from None


def load_config(path: Path) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(path, [f"cannot read file: {exc}"]) from None
    try:
        data = yaml.load(text, Loader=_DecimalLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(path, [f"invalid YAML: {exc}"]) from None
    return parse_config(data, path)


def _format_errors(exc: ValidationError) -> list[str]:
    errors = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        errors.append(f"{loc}: {err['msg']}")
    return errors
