"""outreach: startup directory sources

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-29 22:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('outreach_sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(length=1000), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=True),
    sa.Column('location', sa.String(length=200), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column('last_read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('entries_found', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('added_total', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outreach_sources')),
    sa.UniqueConstraint('url', name=op.f('uq_outreach_sources_url'))
    )
    # the user's first directory
    op.execute(
        "INSERT INTO outreach_sources (url, label, location, enabled, entries_found, added_total) "
        "VALUES ('https://www.bangalorestartupmap.com/', 'Bangalore Startup Map', 'Bengaluru', "
        "true, 0, 0)"
    )


def downgrade() -> None:
    op.drop_table('outreach_sources')
