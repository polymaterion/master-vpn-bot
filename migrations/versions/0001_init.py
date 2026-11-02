"""init: users, payments, pending_orders, webhook_events, admin_audit, reminders

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("tg_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("nav", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_ref", sa.String(128)),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("devices", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("idem_key", sa.String(128)),
        sa.Column("invoice_url", sa.String(512)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("request_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_payment_provider_ref"),
    )
    op.create_index("ix_payments_tg_id", "payments", ["tg_id"])
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_table(
        "pending_orders",
        sa.Column("payment_id", sa.String(36), sa.ForeignKey("payments.id"), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(64)),
        sa.Column("next_try_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("seq", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("details", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "reminders",
        sa.Column("tg_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("expires_at", sa.String(40), primary_key=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for table in ("reminders", "admin_audit", "webhook_events", "pending_orders", "payments", "users"):
        op.drop_table(table)
