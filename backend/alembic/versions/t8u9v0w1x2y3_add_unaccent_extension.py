"""add unaccent extension for punctuation/accent-insensitive title lookup

Revision ID: t8u9v0w1x2y3
Revises: s7t8u9v0w1x2
Create Date: 2026-07-31

Title lookups were exact-string ILIKE, so a Spanish speaker naming a Spanish
title missed it over a diacritic: "la naranja mecanica" does not match the stored
`title_es='La naranja mecánica'`, and "deprisa deprisa" does not match
`original_title='Deprisa, deprisa'` over the comma. Both were silent — the search
fell through to the generic vector path and the user never learned why.

`unaccent` handles the diacritics; the punctuation half is a regexp_replace in
the query itself (see routers.search._match_expression), which needs no
extension. No functional index: `unaccent()` is STABLE rather than IMMUTABLE, so
it cannot be indexed without an IMMUTABLE wrapper, and the lookup runs once per
search over 20k rows. Measure before adding one.
"""
from alembic import op

revision = "t8u9v0w1x2y3"
down_revision = "s7t8u9v0w1x2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    # Left in place on purpose: dropping an extension another migration or a
    # hand-written query may lean on is not worth the rollback tidiness.
    pass
