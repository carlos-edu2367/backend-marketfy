"""marketing_funnel_events e marketing_funnel_leads.

Tabelas dos funis de conversao A/B (/funil-a, /funil-b): eventos de
navegacao anonima (visitante nunca autenticado) e leads reais capturados
no fim do funil. Ver spec 2026-09-15-funis-ab-conversao-design.md.
"""

from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_0026"
down_revision: Union[str, Sequence[str], None] = "20260915_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketing_funnel_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("visitor_id", sa.String(), nullable=False),
        sa.Column("funnel_variant", sa.String(length=1), nullable=False),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_marketing_funnel_events_visitor_id", "marketing_funnel_events", ["visitor_id"])
    op.create_index("ix_marketing_funnel_events_created_at", "marketing_funnel_events", ["created_at"])
    op.create_index(
        "ix_mfe_variant_event_step",
        "marketing_funnel_events",
        ["funnel_variant", "event_name", "step"],
    )

    op.create_table(
        "marketing_funnel_leads",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("visitor_id", sa.String(), nullable=False),
        sa.Column("funnel_variant", sa.String(length=1), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("control_score", sa.Integer(), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_marketing_funnel_leads_visitor_id", "marketing_funnel_leads", ["visitor_id"])
    op.create_index("ix_mfl_variant", "marketing_funnel_leads", ["funnel_variant"])


def downgrade() -> None:
    op.drop_index("ix_mfl_variant", table_name="marketing_funnel_leads")
    op.drop_index("ix_marketing_funnel_leads_visitor_id", table_name="marketing_funnel_leads")
    op.drop_table("marketing_funnel_leads")

    op.drop_index("ix_mfe_variant_event_step", table_name="marketing_funnel_events")
    op.drop_index("ix_marketing_funnel_events_created_at", table_name="marketing_funnel_events")
    op.drop_index("ix_marketing_funnel_events_visitor_id", table_name="marketing_funnel_events")
    op.drop_table("marketing_funnel_events")
