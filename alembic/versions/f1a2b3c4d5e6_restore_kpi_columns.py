"""restore kpi columns to sarimax_models — dropped by accident in d8fb31cb8ab8

Revision ID: f1a2b3c4d5e6
Revises: d8fb31cb8ab8
Create Date: 2026-05-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'd8fb31cb8ab8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {col['name'] for col in sa.inspect(op.get_bind()).get_columns('sarimax_models')}
    to_add = [
        ('total_records',      sa.Column('total_records', sa.Integer(), nullable=True)),
        ('data_quality_pct',   sa.Column('data_quality_pct', sa.Float(), nullable=True)),
        ('revenue_total',      sa.Column('revenue_total', sa.Float(), nullable=True)),
        ('growth_rate',        sa.Column('growth_rate', sa.Float(), nullable=True)),
        ('expected_bookings',  sa.Column('expected_bookings', sa.Integer(), nullable=True)),
        ('peak_travel_period', sa.Column('peak_travel_period', sa.String(100), nullable=True)),
    ]
    for name, col in to_add:
        if name not in existing:
            op.add_column('sarimax_models', col)


def downgrade() -> None:
    op.drop_column('sarimax_models', 'yearly_bookings_json')
    op.drop_column('sarimax_models', 'peak_travel_period')
    op.drop_column('sarimax_models', 'expected_bookings')
    op.drop_column('sarimax_models', 'growth_rate')
    op.drop_column('sarimax_models', 'revenue_total')
    op.drop_column('sarimax_models', 'data_quality_pct')
    op.drop_column('sarimax_models', 'total_records')