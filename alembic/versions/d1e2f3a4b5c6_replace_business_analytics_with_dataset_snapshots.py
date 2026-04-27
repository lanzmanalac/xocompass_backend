"""replace business_analytics_snapshots with dataset_snapshots

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4g5h6
Create Date: 2026-04-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4g5h6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Drop business_analytics_snapshots (replaced by dataset_snapshots) ──
    op.drop_table('business_analytics_snapshots')

    # ── 2. Create dataset_snapshots ──
    op.create_table(
        'dataset_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('ingestion_batch_id', sa.String(36), nullable=False, unique=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_transaction_count', sa.Integer(), nullable=True),
        sa.Column('total_weekly_records', sa.Integer(), nullable=True),
        sa.Column('total_revenue', sa.Float(), nullable=True),
        sa.Column('data_start_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data_end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('span_weeks', sa.Integer(), nullable=True),
        sa.Column('avg_weekly_bookings', sa.Float(), nullable=True),
        sa.Column('peak_week_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('peak_week_bookings', sa.Integer(), nullable=True),
        sa.Column('growth_rate', sa.Float(), nullable=True),
        sa.Column('bookings_by_year_json', sa.JSON(), nullable=True),
        sa.Column('bookings_by_month_json', sa.JSON(), nullable=True),
        sa.Column('holiday_week_count', sa.Integer(), nullable=True),
        sa.Column('non_holiday_week_count', sa.Integer(), nullable=True),
    )

    # ── 3. Add ingestion_batch_id to training_data_log ──
    op.add_column('training_data_log',
        sa.Column('ingestion_batch_id', sa.String(36), nullable=True)
    )

    # ── 4. Add ingestion_batch_id to sarimax_models ──
    op.add_column('sarimax_models',
        sa.Column('ingestion_batch_id', sa.String(36), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('sarimax_models', 'ingestion_batch_id')
    op.drop_column('training_data_log', 'ingestion_batch_id')
    op.drop_table('dataset_snapshots')
    op.create_table(
        'business_analytics_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_transaction_count', sa.Integer(), nullable=True),
        sa.Column('total_weekly_records', sa.Integer(), nullable=True),
        sa.Column('total_revenue', sa.Float(), nullable=True),
        sa.Column('data_start_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data_end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('span_weeks', sa.Integer(), nullable=True),
        sa.Column('avg_weekly_bookings', sa.Float(), nullable=True),
        sa.Column('peak_week_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('peak_week_bookings', sa.Integer(), nullable=True),
        sa.Column('growth_rate', sa.Float(), nullable=True),
        sa.Column('bookings_by_year_json', sa.JSON(), nullable=True),
        sa.Column('bookings_by_month_json', sa.JSON(), nullable=True),
        sa.Column('holiday_week_count', sa.Integer(), nullable=True),
        sa.Column('non_holiday_week_count', sa.Integer(), nullable=True),
    )