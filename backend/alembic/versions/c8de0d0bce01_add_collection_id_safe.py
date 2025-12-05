"""add_collection_id_safe

Revision ID: c8de0d0bce01
Revises: b1d45f76893c
Create Date: 2025-12-04 21:03:54.516391

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8de0d0bce01'
down_revision: Union[str, None] = 'b1d45f76893c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if column exists to be safe, or just add it
    op.add_column('movies', sa.Column('collection_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_movies_collection_id'), 'movies', ['collection_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_movies_collection_id'), table_name='movies')
    op.drop_column('movies', 'collection_id')
