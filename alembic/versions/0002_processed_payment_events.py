"""processed payment events

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15 05:38:03.836353
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('processed_payment_events',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('event_name', sa.String(length=64), nullable=False),
    sa.Column('object_type', sa.String(length=64), nullable=False),
    sa.Column('object_id', sa.String(length=128), nullable=False),
    sa.Column('processed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_processed_payment_events')),
    sa.UniqueConstraint('provider', 'event_name', 'object_type', 'object_id', name=op.f('uq_processed_payment_events_provider'))
    )
    with op.batch_alter_table('orders') as batch_op:
        batch_op.add_column(sa.Column('user_quota_id', sa.String(length=64), nullable=True))
        batch_op.create_index(op.f('ix_orders_user_quota_id'), ['user_quota_id'], unique=False)
        batch_op.create_foreign_key(op.f('fk_orders_user_quota_id_users_quota'), 'users_quota', ['user_quota_id'], ['id'], ondelete='SET NULL')

    # Supabase exposes `public` through its Data API: RLS on, no policies (deny-all for anon/authenticated).
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "processed_payment_events" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table('processed_payment_events')
    with op.batch_alter_table('orders') as batch_op:
        batch_op.drop_constraint(op.f('fk_orders_user_quota_id_users_quota'), type_='foreignkey')
        batch_op.drop_index(op.f('ix_orders_user_quota_id'))
        batch_op.drop_column('user_quota_id')
