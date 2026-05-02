"""add user onboarding fields

Revision ID: h6i7j8k9l0m1
Revises: 07676e980ac2
Create Date: 2026-05-02 19:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'h6i7j8k9l0m1'
down_revision: Union[str, None] = '07676e980ac2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('tag_preferences', postgresql.JSONB(), nullable=True))
    op.add_column('users', sa.Column('onboarding_completed', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('users', sa.Column('onboarding_ratings_count', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'onboarding_ratings_count')
    op.drop_column('users', 'onboarding_completed')
    op.drop_column('users', 'tag_preferences')
