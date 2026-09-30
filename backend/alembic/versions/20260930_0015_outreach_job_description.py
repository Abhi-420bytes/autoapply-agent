"""outreach: optional job description and role per company

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-30 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('outreach_companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('jd_text', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('role', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('outreach_companies', schema=None) as batch_op:
        batch_op.drop_column('role')
        batch_op.drop_column('jd_text')
