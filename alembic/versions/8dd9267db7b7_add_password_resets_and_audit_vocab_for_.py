"""add password_resets and audit vocab for reset

Revision ID: <alembic-generated>
Revises: 0ab1bdfdb999
Create Date: 2026-05-03

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = '<alembic-generated>'
down_revision: Union[str, Sequence[str], None] = '0ab1bdfdb999'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'password_resets',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column('user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('initiated_by_user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True,
                  comment='NULL = self-service; non-null = admin-initiated'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_address_initiated', sa.String(45), nullable=True),
        sa.Column('ip_address_consumed', sa.String(45), nullable=True),
    )
    op.create_index('ix_password_resets_token_hash',
                    'password_resets', ['token_hash'], unique=True)
    op.create_index('ix_password_resets_user_pending',
                    'password_resets', ['user_id', 'consumed_at'])


def downgrade() -> None:
    op.drop_index('ix_password_resets_user_pending', table_name='password_resets')
    op.drop_index('ix_password_resets_token_hash', table_name='password_resets')
    op.drop_table('password_resets')