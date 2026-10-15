"""presigned uploads: job statuses UPLOADING/REJECTED and source object columns

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15

jobs.status stays VARCHAR(10) + CHECK `jobstatus` (ADR-003 decision 4); only the allowed values change.
Downgrade maps UPLOADING/REJECTED rows to FAILED so the old CHECK can be restored; credits reserved by in-flight
uploads are not released and their objects are left to the bucket lifecycle rule.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_STATUSES = ('PENDING', 'PROCESSING', 'DONE', 'FAILED')
NEW_STATUSES = ('UPLOADING', 'PENDING', 'PROCESSING', 'DONE', 'FAILED', 'REJECTED')


def _status_type(values):
    return sa.Enum(*values, name=op.f('jobstatus'), native_enum=False, create_constraint=True, length=10)


def _swap_status_check(old, new) -> None:
    if op.get_bind().dialect.name == 'sqlite':
        # Batch mode recreates the table; existing_type lets it drop the old CHECK.
        with op.batch_alter_table('jobs') as batch_op:
            batch_op.alter_column('status', existing_type=_status_type(old), type_=_status_type(new), existing_nullable=True)
        return
    op.drop_constraint(op.f('jobstatus'), 'jobs', type_='check')
    values = ", ".join(f"'{v}'" for v in new)
    op.create_check_constraint(op.f('jobstatus'), 'jobs', f"status IN ({values})")


def upgrade() -> None:
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.add_column(sa.Column('source_object_key', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('declared_size_bytes', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('declared_content_type', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('upload_expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('rejection_reason', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('credit_pool', sa.String(length=8), nullable=True))
        batch_op.create_unique_constraint(op.f('uq_jobs_source_object_key'), ['source_object_key'])
        batch_op.create_index(op.f('ix_jobs_upload_expires_at'), ['upload_expires_at'], unique=False)
    _swap_status_check(OLD_STATUSES, NEW_STATUSES)


def downgrade() -> None:
    op.execute("UPDATE jobs SET status = 'FAILED' WHERE status IN ('UPLOADING', 'REJECTED')")
    _swap_status_check(NEW_STATUSES, OLD_STATUSES)
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_index(op.f('ix_jobs_upload_expires_at'))
        batch_op.drop_constraint(op.f('uq_jobs_source_object_key'), type_='unique')
        batch_op.drop_column('credit_pool')
        batch_op.drop_column('rejection_reason')
        batch_op.drop_column('upload_expires_at')
        batch_op.drop_column('declared_content_type')
        batch_op.drop_column('declared_size_bytes')
        batch_op.drop_column('source_object_key')
