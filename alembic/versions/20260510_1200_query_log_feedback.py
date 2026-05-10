"""add feedback fields to query_logs

Revision ID: a1f4c8e3b201
Revises: 090b3c671031
Create Date: 2026-05-10 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1f4c8e3b201"
down_revision: Union[str, None] = "090b3c671031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("query_logs", sa.Column("rating", sa.Integer(), nullable=True))
    op.add_column("query_logs", sa.Column("feedback_text", sa.Text(), nullable=True))
    op.add_column("query_logs", sa.Column("feedback_at", sa.DateTime(), nullable=True))
    op.create_index("ix_query_logs_rating", "query_logs", ["rating"])


def downgrade() -> None:
    op.drop_index("ix_query_logs_rating", table_name="query_logs")
    op.drop_column("query_logs", "feedback_at")
    op.drop_column("query_logs", "feedback_text")
    op.drop_column("query_logs", "rating")
