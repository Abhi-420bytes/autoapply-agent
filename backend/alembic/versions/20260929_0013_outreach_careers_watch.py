"""outreach: watch careers pages when no email is found

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29 20:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('outreach_companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('careers_url', sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column('watch_checked_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('seen_openings', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    with op.batch_alter_table('outreach_companies', schema=None) as batch_op:
        batch_op.drop_column('seen_openings')
        batch_op.drop_column('watch_checked_at')
        batch_op.drop_column('careers_url')
