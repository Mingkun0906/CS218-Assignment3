"""create orders, ledger, and idempotency_records tables

Revision ID: 0001
Revises: 
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # orders
    op.create_table(
        "orders",
        sa.Column("order_id",    sa.Text, primary_key=True, nullable=False),
        sa.Column("customer_id", sa.Text, nullable=False),
        sa.Column("item_id",     sa.Text, nullable=False),
        sa.Column(
            "quantity",
            sa.Integer,
            nullable=False,
            comment="Must be positive",
        ),
        sa.Column("status",     sa.Text, nullable=False, server_default="created"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_orders_quantity_positive"),
    )
    op.create_index("idx_orders_customer_id", "orders", ["customer_id"])

    # ledger
    op.create_table(
        "ledger",
        sa.Column("ledger_id", sa.Text, primary_key=True, nullable=False),
        sa.Column("order_id",  sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.order_id"],
            name="fk_ledger_order_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_ledger_order_id", "ledger", ["order_id"])

    # idempotency_records
    op.create_table(
        "idempotency_records",
        sa.Column("idempotency_key", sa.Text, primary_key=True, nullable=False),
        sa.Column("request_hash",   sa.Text, nullable=False),
        sa.Column("response_body",  sa.Text, nullable=False),
        sa.Column("status_code",    sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_index("idx_ledger_order_id",    table_name="ledger")
    op.drop_table("ledger")
    op.drop_index("idx_orders_customer_id", table_name="orders")
    op.drop_table("orders")
