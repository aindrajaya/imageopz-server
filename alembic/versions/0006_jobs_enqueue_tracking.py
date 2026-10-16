"""jobs.enqueued_at and jobs.enqueue_attempts (Cloud Tasks job processing, ADR-005)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16

enqueued_at: when the job last went to the queue (stale-PENDING re-enqueue by the sweeper, U8).
enqueue_attempts: deliberate re-enqueues so far; task names get "-r<n>" because Cloud Tasks keeps a used name
reserved for up to 24 hours. Existing rows get NULL / 0. Downgrade drops both columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.add_column(sa.Column('enqueued_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('enqueue_attempts', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_column('enqueue_attempts')
        batch_op.drop_column('enqueued_at')
