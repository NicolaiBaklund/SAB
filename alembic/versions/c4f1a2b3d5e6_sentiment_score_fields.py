"""sentiment relevance, rationale, prompt_version

Revision ID: c4f1a2b3d5e6
Revises: 91547394b2c4
Create Date: 2026-06-09 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f1a2b3d5e6'
down_revision: Union[str, Sequence[str], None] = '91547394b2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Phase 2.1 widens the sentiment row with the scorer's extra outputs. All three
# are nullable: SQLite ``ADD COLUMN`` needs no table rebuild, and leaving them
# nullable keeps the migration safe even if pre-existing sentiment rows are
# present. The scorer always populates ``relevance`` and ``prompt_version``;
# ``rationale`` may be empty if a model returns none.
#  - relevance       : direct | mentioned | off_topic (keyword-match quality)
#  - rationale        : one-line model justification, for GUI review
#  - prompt_version   : which prompt template produced the score (audit trail)
def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("sentiment", sa.Column("relevance", sa.Text(), nullable=True))
    op.add_column("sentiment", sa.Column("rationale", sa.Text(), nullable=True))
    op.add_column("sentiment", sa.Column("prompt_version", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sentiment", "prompt_version")
    op.drop_column("sentiment", "rationale")
    op.drop_column("sentiment", "relevance")
