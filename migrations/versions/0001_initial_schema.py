"""Initial schema (spec section 6) and NOTIFY on events insert

Revision ID: 0001
Revises:
Create Date: 2026-10-09 14:57:02.302526
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# The payload carries only the id so it stays far below the 8000-byte
# NOTIFY limit; listeners fetch the row.
NOTIFY_FUNCTION = """
CREATE FUNCTION notify_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify(
        'events',
        json_build_object(
            'id', NEW.id, 'severity', NEW.severity, 'type', NEW.type
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

NOTIFY_TRIGGER = """
CREATE TRIGGER events_notify AFTER INSERT ON events
FOR EACH ROW EXECUTE FUNCTION notify_event()
"""

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "balance_snapshots",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wallet", sa.Text(), nullable=False),
        sa.Column("asset", sa.Text(), nullable=False),
        sa.Column("free", sa.Numeric(), nullable=False),
        sa.Column("locked", sa.Numeric(), nullable=False),
        sa.CheckConstraint(
            "wallet IN ('spot', 'futures')",
            name=op.f("ck_balance_snapshots_wallet"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_balance_snapshots")),
    )
    op.create_index(
        op.f("ix_balance_snapshots_ts"),
        "balance_snapshots",
        ["ts"],
        unique=False,
    )
    op.create_table(
        "config_versions",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column(
            "config", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_versions")),
        sa.UniqueConstraint("hash", name=op.f("uq_config_versions_hash")),
    )
    op.create_table(
        "events",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARN', 'CRIT')",
            name=op.f("ck_events_severity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(op.f("ix_events_ts"), "events", ["ts"], unique=False)
    op.create_index(
        op.f("ix_events_type_ts"), "events", ["type", "ts"], unique=False
    )
    op.create_table(
        "funding_payments",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("venue_payment_id", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("asset", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_payments")),
        sa.UniqueConstraint(
            "venue_payment_id",
            name=op.f("uq_funding_payments_venue_payment_id"),
        ),
    )
    op.create_index(
        op.f("ix_funding_payments_symbol_ts"),
        "funding_payments",
        ["symbol", "ts"],
        unique=False,
    )
    op.create_table(
        "funding_rates",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("rate", sa.Numeric(), nullable=False),
        sa.Column("predicted_rate", sa.Numeric(), nullable=True),
        sa.Column("interval_s", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_funding_rates")),
        sa.UniqueConstraint(
            "symbol", "ts", name=op.f("uq_funding_rates_symbol_ts")
        ),
    )
    op.create_table(
        "instruments",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("venue", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("venue_symbol", sa.Text(), nullable=False),
        sa.Column("tick_size", sa.Numeric(), nullable=False),
        sa.Column("step_size", sa.Numeric(), nullable=False),
        sa.Column("min_notional", sa.Numeric(), nullable=False),
        sa.Column("contract_size", sa.Numeric(), nullable=True),
        sa.Column("funding_interval_s", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('spot', 'perp')", name=op.f("ck_instruments_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint(
            "venue",
            "symbol",
            "type",
            name=op.f("uq_instruments_venue_symbol_type"),
        ),
    )
    op.create_table(
        "orders",
        sa.Column("client_order_id", sa.Text(), nullable=False),
        sa.Column("venue_order_id", sa.Text(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("leg", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=True),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "leg IN ('spot', 'perp')", name=op.f("ck_orders_leg")
        ),
        sa.CheckConstraint(
            "side IN ('buy', 'sell')", name=op.f("ck_orders_side")
        ),
        sa.PrimaryKeyConstraint("client_order_id", name=op.f("pk_orders")),
    )
    op.create_index(
        op.f("ix_orders_symbol_created_at"),
        "orders",
        ["symbol", "created_at"],
        unique=False,
    )
    op.create_table(
        "pnl_daily",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("nav", sa.Numeric(), nullable=False),
        sa.Column("funding", sa.Numeric(), nullable=False),
        sa.Column("fees", sa.Numeric(), nullable=False),
        sa.Column("basis_pnl", sa.Numeric(), nullable=False),
        sa.Column("delta_pnl", sa.Numeric(), nullable=False),
        sa.Column("total", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("date", name=op.f("pk_pnl_daily")),
    )
    op.create_table(
        "position_snapshots",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("spot_qty", sa.Numeric(), nullable=False),
        sa.Column("perp_qty", sa.Numeric(), nullable=False),
        sa.Column("net_delta_usd", sa.Numeric(), nullable=False),
        sa.Column("liq_price", sa.Numeric(), nullable=True),
        sa.Column("liq_distance", sa.Numeric(), nullable=True),
        sa.Column("margin_ratio", sa.Numeric(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position_snapshots")),
    )
    op.create_index(
        op.f("ix_position_snapshots_symbol_ts"),
        "position_snapshots",
        ["symbol", "ts"],
        unique=False,
    )
    op.create_table(
        "price_snapshots",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("spot_bid", sa.Numeric(), nullable=False),
        sa.Column("spot_ask", sa.Numeric(), nullable=False),
        sa.Column("perp_mark", sa.Numeric(), nullable=False),
        sa.Column("index_price", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_snapshots")),
    )
    op.create_index(
        op.f("ix_price_snapshots_symbol_ts"),
        "price_snapshots",
        ["symbol", "ts"],
        unique=False,
    )
    op.create_table(
        "risk_decisions",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_name", sa.Text(), nullable=False),
        sa.Column(
            "inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('allow', 'scale', 'deny', 'halt')",
            name=op.f("ck_risk_decisions_outcome"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_decisions")),
    )
    op.create_index(
        op.f("ix_risk_decisions_ts"), "risk_decisions", ["ts"], unique=False
    )
    op.create_table(
        "fills",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("venue_fill_id", sa.Text(), nullable=False),
        sa.Column("client_order_id", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("fee", sa.Numeric(), nullable=False),
        sa.Column("fee_asset", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_order_id"],
            ["orders.client_order_id"],
            name=op.f("fk_fills_client_order_id_orders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fills")),
        sa.UniqueConstraint(
            "client_order_id",
            "venue_fill_id",
            name=op.f("uq_fills_client_order_id_venue_fill_id"),
        ),
    )
    op.execute(NOTIFY_FUNCTION)
    op.execute(NOTIFY_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER events_notify ON events")
    op.execute("DROP FUNCTION notify_event()")
    op.drop_table("fills")
    op.drop_index(op.f("ix_risk_decisions_ts"), table_name="risk_decisions")
    op.drop_table("risk_decisions")
    op.drop_index(
        op.f("ix_price_snapshots_symbol_ts"), table_name="price_snapshots"
    )
    op.drop_table("price_snapshots")
    op.drop_index(
        op.f("ix_position_snapshots_symbol_ts"), table_name="position_snapshots"
    )
    op.drop_table("position_snapshots")
    op.drop_table("pnl_daily")
    op.drop_index(op.f("ix_orders_symbol_created_at"), table_name="orders")
    op.drop_table("orders")
    op.drop_table("instruments")
    op.drop_table("funding_rates")
    op.drop_index(
        op.f("ix_funding_payments_symbol_ts"), table_name="funding_payments"
    )
    op.drop_table("funding_payments")
    op.drop_index(op.f("ix_events_type_ts"), table_name="events")
    op.drop_index(op.f("ix_events_ts"), table_name="events")
    op.drop_table("events")
    op.drop_table("config_versions")
    op.drop_index(
        op.f("ix_balance_snapshots_ts"), table_name="balance_snapshots"
    )
    op.drop_table("balance_snapshots")
