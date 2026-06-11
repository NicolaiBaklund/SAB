"""create prices table

Revision ID: e2a8d4c1f7b9
Revises: c4f1a2b3d5e6
Create Date: 2026-06-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2a8d4c1f7b9'
down_revision: Union[str, Sequence[str], None] = 'c4f1a2b3d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Phase 3.1: one daily OHLCV bar per (ticker, trading day), upserted by the
# price fetcher (src/data/prices.py). `close` is the only required price field:
# the fetcher drops bars without a close, while open/high/low/volume can be
# missing on the source side. `adj_close` is the dividend/split-adjusted series
# used for returns and technical indicators.
def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('prices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ticker', sa.Text(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('open', sa.Float(), nullable=True),
    sa.Column('high', sa.Float(), nullable=True),
    sa.Column('low', sa.Float(), nullable=True),
    sa.Column('close', sa.Float(), nullable=False),
    sa.Column('adj_close', sa.Float(), nullable=True),
    sa.Column('volume', sa.Integer(), nullable=True),
    sa.Column('currency', sa.Text(), nullable=True),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ticker', 'date', name='uq_prices_ticker_date')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('prices')
