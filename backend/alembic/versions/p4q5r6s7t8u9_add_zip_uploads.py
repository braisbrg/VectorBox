"""add_zip_uploads

Tracks Letterboxd ZIP imports so re-upload is idempotent (F-35):

  - sha256 keys the upload — same bytes never get reprocessed.
  - max_watched_date captures the freshness of the ZIP at upload time;
    a later upload with an older max_watched_date is flagged as a warning
    (user might be uploading a stale backup).

UNIQUE(user_id, sha256) — same user, same file = no-op.
Two users uploading identical ZIPs is fine (separate rows).

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
Create Date: 2026-05-15

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'p4q5r6s7t8u9'
down_revision: Union[str, None] = 'o3p4q5r6s7t8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'zip_uploads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('films_count', sa.Integer(), nullable=True),
        sa.Column('max_watched_date', sa.Date(), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.UniqueConstraint('user_id', 'sha256', name='uq_zip_uploads_user_sha'),
    )
    op.create_index('idx_zip_uploads_user_processed', 'zip_uploads', ['user_id', sa.text('processed_at DESC')])


def downgrade() -> None:
    op.drop_index('idx_zip_uploads_user_processed', table_name='zip_uploads')
    op.drop_table('zip_uploads')
