"""add lead time and airlines to dataset_snapshots

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-04-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('dataset_snapshots',
        sa.Column('avg_lead_time_days', sa.Float(), nullable=True))
    op.add_column('dataset_snapshots',
        sa.Column('lead_time_distribution_json', sa.JSON(), nullable=True))
    op.add_column('dataset_snapshots',
        sa.Column('top_airlines_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('dataset_snapshots', 'top_airlines_json')
    op.drop_column('dataset_snapshots', 'lead_time_distribution_json')
    op.drop_column('dataset_snapshots', 'avg_lead_time_days')