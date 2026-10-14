"""payments.webhook_deliveries (Go ingress dedupe table)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15

PostgreSQL only: the table lives in its own `payments` schema and is written solely by the Go ingress
(services/payments-go). It is deliberately not part of the SQLAlchemy models; alembic/env.py excludes the
schema from autogenerate comparison. No-op on SQLite.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute("CREATE SCHEMA payments")
    op.create_table('webhook_deliveries',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('event_name', sa.String(length=64), nullable=False),
    sa.Column('object_type', sa.String(length=64), nullable=False),
    sa.Column('object_id', sa.String(length=128), nullable=False),
    sa.Column('task_name', sa.String(length=512), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_deliveries')),
    sa.UniqueConstraint('provider', 'event_name', 'object_type', 'object_id', name=op.f('uq_webhook_deliveries_provider')),
    schema='payments',
    )
    # Not exposed through the Supabase Data API (only `public` is), but deny-all RLS stays the house rule.
    op.execute('ALTER TABLE payments.webhook_deliveries ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    if not _is_postgres():
        return
    op.drop_table('webhook_deliveries', schema='payments')
    op.execute("DROP SCHEMA payments")
