"""add auth and audit tables

Revision ID: 37c7b3a88ff7
Revises: 427ebce053a4
Create Date: 2026-04-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID


revision: str = '37c7b3a88ff7'
down_revision: Union[str, Sequence[str], None] = 'ba5645624e03'
branch_labels = None
depends_on = None

# ── Declare ENUM types as standalone objects with create_type=False ──────────
# create_type=False is the ONLY way to fully suppress SQLAlchemy's internal
# _on_table_create hook from firing a second CREATE TYPE statement.
# We control type creation exclusively via op.execute() below.
user_role_enum   = PG_ENUM('ADMIN', 'ANALYST', 'VIEWER', name='user_role',   create_type=False)
audit_status_enum = PG_ENUM('SUCCESS', 'FAILURE', 'PENDING', name='audit_status', create_type=False)


def upgrade() -> None:
    # ── Step 1: Create ENUM types idempotently via raw DDL ───────────────────
    # We use raw op.execute() because only raw SQL supports IF NOT EXISTS for
    # CREATE TYPE in PostgreSQL. SQLAlchemy's ORM layer does not expose this.
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE user_role AS ENUM ('ADMIN', 'ANALYST', 'VIEWER');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE audit_status AS ENUM ('SUCCESS', 'FAILURE', 'PENDING');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ── Step 2: Create tables — referencing pre-existing ENUMs by name only ──
    op.create_table(
        'users',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('full_name', sa.String(120), nullable=False),          # was 'name'
        sa.Column('hashed_password', sa.Text(), nullable=False),
        sa.Column('role', user_role_enum, nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),   # ADD
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    op.create_table(
        'refresh_tokens',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(255), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default='false'),
    )

    op.create_table(
        'invite_tokens',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('token', sa.String(255), nullable=False, unique=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('role', user_role_enum, nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('action_type', sa.String(80), nullable=False),
        sa.Column('module', sa.String(80), nullable=False),
        sa.Column('status', audit_status_enum, nullable=False),
        sa.Column('user_email_snapshot', sa.String(255), nullable=True),
        sa.Column('extra_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'global_settings',
        sa.Column('key', sa.String(120), primary_key=True),
        sa.Column('value_json', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── Step 3: Seed default global settings (idempotent) ────────────────────
    op.execute("""
        INSERT INTO global_settings (key, value_json) VALUES
            ('booking_volume_benchmark',     '{"value": 100}'),
            ('default_date_range_weeks',     '{"value": 12}'),
            ('forecast_deviation_alert_pct', '{"value": 15.0}')
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table('global_settings')
    op.drop_table('audit_logs')
    op.drop_table('invite_tokens')
    op.drop_table('refresh_tokens')
    op.drop_table('users')
    # Drop ENUMs after tables — CASCADE handles any leftover dependencies
    op.execute("DROP TYPE IF EXISTS audit_status")
    op.execute("DROP TYPE IF EXISTS user_role")