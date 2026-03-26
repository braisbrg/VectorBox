"""add has_enriched_embedding and medoid_movie_id

Revision ID: a1b2c3d4e5f6
Revises: 030af05b935f
Create Date: 2026-03-25 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '030af05b935f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('movies', sa.Column('has_enriched_embedding', sa.Boolean(), server_default='false', nullable=True))
    op.add_column('user_clusters', sa.Column('medoid_movie_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_clusters', 'medoid_movie_id')
    op.drop_column('movies', 'has_enriched_embedding')
