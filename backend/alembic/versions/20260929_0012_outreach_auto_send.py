"""outreach: company size and automatic sending

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-29 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('outreach_companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('size', sa.String(length=20), nullable=True))
    with op.batch_alter_table('outreach_emails', schema=None) as batch_op:
        batch_op.add_column(sa.Column('auto_send_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_outreach_emails_auto_send_at'), ['auto_send_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('outreach_emails', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_outreach_emails_auto_send_at'))
        batch_op.drop_column('auto_send_at')
    with op.batch_alter_table('outreach_companies', schema=None) as batch_op:
        batch_op.drop_column('size')
