"""composite unique ticker url

Revision ID: 91547394b2c4
Revises: 5d3286121b78
Create Date: 2026-06-09 13:20:40.904499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91547394b2c4'
down_revision: Union[str, Sequence[str], None] = '5d3286121b78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Swap the articles uniqueness from UNIQUE(url) to UNIQUE(ticker, url) so one
# news article can be stored once per company it mentions (RSS scraper, Phase
# 1.5). SQLite cannot ALTER a constraint in place, so we rebuild the table with
# batch mode. ``copy_from`` gives alembic an explicit picture of the *current*
# table (the original UNIQUE(url) was unnamed) so the drop/create below apply to
# a freshly built table and the 115+ existing rows are copied across intact.
def _articles_table() -> sa.Table:
    meta = sa.MetaData()
    return sa.Table(
        "articles",
        meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("published", sa.DateTime()),
        sa.Column("title", sa.Text()),
        sa.Column("body", sa.Text()),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("url", name="uq_articles_url"),
    )


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table(
        "articles", copy_from=_articles_table(), recreate="always"
    ) as batch:
        batch.drop_constraint("uq_articles_url", type_="unique")
        batch.create_unique_constraint("uq_articles_ticker_url", ["ticker", "url"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("articles", recreate="always") as batch:
        batch.drop_constraint("uq_articles_ticker_url", type_="unique")
        batch.create_unique_constraint("uq_articles_url", ["url"])
