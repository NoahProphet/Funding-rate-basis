"""PostgreSQL schema (spec section 6). Alembic migrations must match this.

Money and quantities are unbounded NUMERIC so Decimal round-trips exactly.
Enumerations are TEXT with CHECK constraints, which are cheaper to migrate
than native enum types.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# Bump together with every new migration; core refuses to start on a
# database at any other revision. tests/test_schema.py enforces this.
SCHEMA_REVISION = "0001"

# Postgres NOTIFY channel fed by a trigger on events inserts (migration
# 0001). Payload: {"id", "severity", "type"}.
EVENTS_CHANNEL = "events"

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

SEVERITIES = ("INFO", "WARN", "CRIT")
RISK_OUTCOMES = ("allow", "scale", "deny", "halt")
INSTRUMENT_TYPES = ("spot", "perp")
LEGS = ("spot", "perp")
SIDES = ("buy", "sell")
WALLETS = ("spot", "futures")


def _num(name: str, nullable: bool = False) -> sa.Column[Decimal]:
    return sa.Column(name, sa.Numeric(asdecimal=True), nullable=nullable)


def _ts(name: str = "ts") -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def _id() -> sa.Column[int]:
    return sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True)


def _one_of(column: str, values: Iterable[str]) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=column)


instruments = sa.Table(
    "instruments",
    metadata,
    _id(),
    sa.Column("venue", sa.Text, nullable=False),
    sa.Column("symbol", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("venue_symbol", sa.Text, nullable=False),
    _num("tick_size"),
    _num("step_size"),
    _num("min_notional"),
    _num("contract_size", nullable=True),
    sa.Column("funding_interval_s", sa.Integer, nullable=True),
    _ts("updated_at"),
    sa.UniqueConstraint("venue", "symbol", "type"),
    _one_of("type", INSTRUMENT_TYPES),
)

funding_rates = sa.Table(
    "funding_rates",
    metadata,
    _id(),
    _ts(),
    sa.Column("symbol", sa.Text, nullable=False),
    _num("rate"),
    _num("predicted_rate", nullable=True),
    sa.Column("interval_s", sa.Integer, nullable=False),
    sa.UniqueConstraint("symbol", "ts"),
)

price_snapshots = sa.Table(
    "price_snapshots",
    metadata,
    _id(),
    _ts(),
    sa.Column("symbol", sa.Text, nullable=False),
    _num("spot_bid"),
    _num("spot_ask"),
    _num("perp_mark"),
    _num("index_price"),
    sa.Index(None, "symbol", "ts"),
)

orders = sa.Table(
    "orders",
    metadata,
    sa.Column("client_order_id", sa.Text, primary_key=True),
    sa.Column("venue_order_id", sa.Text, nullable=True),
    sa.Column("symbol", sa.Text, nullable=False),
    sa.Column("leg", sa.Text, nullable=False),
    sa.Column("side", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    _num("price", nullable=True),
    _num("qty"),
    sa.Column("status", sa.Text, nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
    sa.Column("reason", sa.Text, nullable=True),
    _one_of("leg", LEGS),
    _one_of("side", SIDES),
    sa.Index(None, "symbol", "created_at"),
)

fills = sa.Table(
    "fills",
    metadata,
    _id(),
    sa.Column("venue_fill_id", sa.Text, nullable=False),
    sa.Column(
        "client_order_id",
        sa.Text,
        sa.ForeignKey("orders.client_order_id"),
        nullable=False,
    ),
    _num("price"),
    _num("qty"),
    _num("fee"),
    sa.Column("fee_asset", sa.Text, nullable=False),
    _ts(),
    # Venue trade ids are not guaranteed unique across spot and perp.
    sa.UniqueConstraint("client_order_id", "venue_fill_id"),
)

position_snapshots = sa.Table(
    "position_snapshots",
    metadata,
    _id(),
    _ts(),
    sa.Column("symbol", sa.Text, nullable=False),
    _num("spot_qty"),
    _num("perp_qty"),
    _num("net_delta_usd"),
    _num("liq_price", nullable=True),
    _num("liq_distance", nullable=True),
    _num("margin_ratio", nullable=True),
    sa.Index(None, "symbol", "ts"),
)

balance_snapshots = sa.Table(
    "balance_snapshots",
    metadata,
    _id(),
    _ts(),
    sa.Column("wallet", sa.Text, nullable=False),
    sa.Column("asset", sa.Text, nullable=False),
    _num("free"),
    _num("locked"),
    _one_of("wallet", WALLETS),
    sa.Index(None, "ts"),
)

funding_payments = sa.Table(
    "funding_payments",
    metadata,
    _id(),
    sa.Column("venue_payment_id", sa.Text, nullable=False, unique=True),
    _ts(),
    sa.Column("symbol", sa.Text, nullable=False),
    sa.Column("asset", sa.Text, nullable=False),
    _num("amount"),
    sa.Index(None, "symbol", "ts"),
)

pnl_daily = sa.Table(
    "pnl_daily",
    metadata,
    sa.Column("date", sa.Date, primary_key=True),
    _num("nav"),
    _num("funding"),
    _num("fees"),
    _num("basis_pnl"),
    _num("delta_pnl"),
    _num("total"),
)

events = sa.Table(
    "events",
    metadata,
    _id(),
    _ts(),
    sa.Column("severity", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("key", sa.Text, nullable=True),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    _one_of("severity", SEVERITIES),
    sa.Index(None, "ts"),
    sa.Index(None, "type", "ts"),
)

risk_decisions = sa.Table(
    "risk_decisions",
    metadata,
    _id(),
    _ts(),
    # "check" is a reserved word in SQL.
    sa.Column("check_name", sa.Text, nullable=False),
    sa.Column("inputs", JSONB, nullable=False),
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
    _one_of("outcome", RISK_OUTCOMES),
    sa.Index(None, "ts"),
)

config_versions = sa.Table(
    "config_versions",
    metadata,
    _id(),
    _ts(),
    sa.Column("hash", sa.Text, nullable=False, unique=True),
    sa.Column("config", JSONB, nullable=False),
)
