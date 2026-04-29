"""merge split heads

Revision ID: 3c51c5cef1ba
Revises: d8fb31cb8ab8, f1g2h3i4j5k6
Create Date: 2026-04-29 22:47:48.720631

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c51c5cef1ba'
down_revision: Union[str, Sequence[str], None] = ('d8fb31cb8ab8', 'f1g2h3i4j5k6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
