"""knowledge_documents: add tags array

Revision ID: b2c7e5f19a44
Revises: a1f4c8e3b201
Create Date: 2026-07-30 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b2c7e5f19a44"
down_revision: Union[str, None] = "a1f4c8e3b201"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=64)),
            nullable=False,
            server_default="{}",
        ),
    )
    # GIN index acelera filtros de overlap/contains por tag.
    op.create_index(
        "ix_knowledge_documents_tags",
        "knowledge_documents",
        ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_documents_tags", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "tags")
