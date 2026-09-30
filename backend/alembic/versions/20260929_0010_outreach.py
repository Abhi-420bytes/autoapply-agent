"""outreach companies and cold emails

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-29 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('outreach_companies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('domain', sa.String(length=200), nullable=False),
    sa.Column('website', sa.String(length=500), nullable=False),
    sa.Column('location', sa.String(length=200), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False, server_default='search'),
    sa.Column('source_query', sa.String(length=500), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False, server_default='new'),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('emails', sa.JSON(), nullable=False, server_default='[]'),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('researched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outreach_companies')),
    sa.UniqueConstraint('domain', name=op.f('uq_outreach_companies_domain'))
    )
    with op.batch_alter_table('outreach_companies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outreach_companies_status'), ['status'], unique=False)
    op.create_table('outreach_emails',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('resume_id', sa.Integer(), nullable=True),
    sa.Column('to_address', sa.String(length=320), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=True),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False, server_default='resume_pending'),
    sa.Column('warnings', sa.JSON(), nullable=False, server_default='[]'),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider_message_id', sa.String(length=300), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['outreach_companies.id'], name=op.f('fk_outreach_emails_company_id_outreach_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_outreach_emails_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['resume_id'], ['resumes.id'], name=op.f('fk_outreach_emails_resume_id_resumes'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outreach_emails')),
    sa.UniqueConstraint('company_id', name=op.f('uq_outreach_emails_company_id'))
    )
    with op.batch_alter_table('outreach_emails', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outreach_emails_status'), ['status'], unique=False)


def downgrade() -> None:
    op.drop_table('outreach_emails')
    op.drop_table('outreach_companies')
