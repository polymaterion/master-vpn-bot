"""users: username, first_name, last_name, source (метка источника)

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(64)))
    op.add_column("users", sa.Column("first_name", sa.String(256)))
    op.add_column("users", sa.Column("last_name", sa.String(256)))
    op.add_column("users", sa.Column("source", sa.String(64)))
    op.create_index("ix_users_source", "users", ["source"])


def downgrade() -> None:
    op.drop_index("ix_users_source", table_name="users")
    for c in ("source", "last_name", "first_name", "username"):
        op.drop_column("users", c)
