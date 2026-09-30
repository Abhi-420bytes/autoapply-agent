"""job level (fresher / experienced)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29 16:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('level', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('level_reason', sa.String(length=200), nullable=True))
        batch_op.create_index(batch_op.f('ix_jobs_level'), ['level'], unique=False)
    # label existing jobs (display only: their status is left as it is)
    from app.generation.level import classify_level

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, role, jd_text FROM jobs")).fetchall()
    for job_id, role, jd in rows:
        level, reason = classify_level(role, jd)
        conn.execute(
            sa.text("UPDATE jobs SET level = :l, level_reason = :r WHERE id = :i"),
            {"l": level, "r": reason[:200], "i": job_id},
        )


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_level'))
        batch_op.drop_column('level_reason')
        batch_op.drop_column('level')
