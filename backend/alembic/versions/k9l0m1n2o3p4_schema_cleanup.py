"""schema_cleanup

Revision ID: k9l0m1n2o3p4
Revises: j8k9l0m1n2o3
Create Date: 2026-05-07 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'k9l0m1n2o3p4'
down_revision = 'j8k9l0m1n2o3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create api_budget table
    op.create_table(
        'api_budget',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('omdb_calls_used', sa.Integer(), server_default='0', nullable=True),
        sa.Column('omdb_calls_limit', sa.Integer(), server_default='1000', nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date')
    )

    # 2. Add last_enriched column to movies
    op.add_column('movies', sa.Column('last_enriched', sa.DateTime(), nullable=True))

    # 3. Drop rotten_tomatoes_rating from movies
    op.drop_column('movies', 'rotten_tomatoes_rating')


def downgrade() -> None:
    # 3. Add rotten_tomatoes_rating back to movies
    op.add_column('movies', sa.Column('rotten_tomatoes_rating', sa.Integer(), nullable=True))

    # 2. Drop last_enriched column from movies
    op.drop_column('movies', 'last_enriched')

    # 1. Drop api_budget table
    op.drop_table('api_budget')
