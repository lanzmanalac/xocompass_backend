"""merge_heads_into_single_chain

Revision ID: 3274a8d87eb5
Revises: 4c3b21b7e32f, f1a2b3c4d5e6
Create Date: 2026-05-01 22:39:02.245999

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3274a8d87eb5'
down_revision: Union[str, Sequence[str], None] = ('4c3b21b7e32f', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
