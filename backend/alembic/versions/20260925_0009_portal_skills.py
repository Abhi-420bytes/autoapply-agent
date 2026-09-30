"""portal skills (learned apply steps)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-25 12:30:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('portal_skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('portal_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=40), nullable=False),
    sa.Column('selector', sa.String(length=500), nullable=False),
    sa.Column('key', sa.String(length=200), nullable=True),
    sa.Column('value_path', sa.String(length=200), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False, server_default='heuristic'),
    sa.Column('successes', sa.Float(), nullable=False, server_default='0'),
    sa.Column('failures', sa.Float(), nullable=False, server_default='0'),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['portal_id'], ['portals.id'], name=op.f('fk_portal_skills_portal_id_portals'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_portal_skills')),
    sa.UniqueConstraint('portal_id', 'role', 'selector', name=op.f('uq_portal_skills_portal_id'))
    )
    with op.batch_alter_table('portal_skills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_portal_skills_portal_id'), ['portal_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('portal_skills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_portal_skills_portal_id'))
    op.drop_table('portal_skills')
