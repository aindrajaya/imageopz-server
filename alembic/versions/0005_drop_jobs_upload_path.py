"""drop jobs.upload_path (local-disk uploads removed with /optimize)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15

Downgrade re-adds the column nullable and empty; the local-disk paths it held are not restored.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode recreates the table on SQLite; on Postgres it is a plain ALTER TABLE.
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_column('upload_path')


def downgrade() -> None:
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.add_column(sa.Column('upload_path', sa.String(length=512), nullable=True))
