"""merge_auth_into_phase1

Revision ID: e22374104ae6
Revises: 37c7b3a88ff7, 427ebce053a4
Create Date: 2026-05-02 18:51:12.644560

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e22374104ae6'
down_revision: Union[str, Sequence[str], None] = ('37c7b3a88ff7', '427ebce053a4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
