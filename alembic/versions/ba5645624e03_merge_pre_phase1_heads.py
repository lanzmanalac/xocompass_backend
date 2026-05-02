"""merge_pre_phase1_heads

Revision ID: ba5645624e03
Revises: <keep alembic's value>, 427ebce053a4
Create Date: 2026-05-02 18:37:30.405846

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba5645624e03'
down_revision: Union[str, Sequence[str], None] = ('3274a8d87eb5', '4c3b21b7e32f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
