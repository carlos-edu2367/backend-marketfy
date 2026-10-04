"""Funis de venda: funnels, variants, steps, sessions, events."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261004_0023"
down_revision: Union[str, Sequence[str], None] = "20260914_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "funnels",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("plan_id", UUID, sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("tracking_html", sa.Text(), nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_funnels_status"),
    )
    op.create_index("ix_funnels_slug", "funnels", ["slug"], unique=True)

    op.create_table(
        "funnel_variants",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("funnel_id", UUID, sa.ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("weight >= 0", name="ck_funnel_variants_weight"),
    )
    op.create_index("ix_funnel_variants_funnel_id", "funnel_variants", ["funnel_id"])

    op.create_table(
        "funnel_steps",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("variant_id", UUID, sa.ForeignKey("funnel_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("html", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("variant_id", "position", name="uq_funnel_steps_variant_position"),
    )
    op.create_index("ix_funnel_steps_variant_id", "funnel_steps", ["variant_id"])

    op.create_table(
        "funnel_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("funnel_id", UUID, sa.ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", UUID, sa.ForeignKey("funnel_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("utm_source", sa.String(200)),
        sa.Column("utm_medium", sa.String(200)),
        sa.Column("utm_campaign", sa.String(200)),
        sa.Column("utm_content", sa.String(200)),
        sa.Column("utm_term", sa.String(200)),
        sa.Column("referrer", sa.String(500)),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("last_step_position", sa.Integer()),
        sa.Column("completed_at", TS),
        sa.Column("registered_at", TS),
        sa.Column("trial_at", TS),
        sa.Column("subscribed_at", TS),
        sa.Column("paid_at", TS),
        sa.Column("first_payment_amount", sa.Numeric(10, 2)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_funnel_sessions_funnel_created", "funnel_sessions", ["funnel_id", "created_at"])
    op.create_index("ix_funnel_sessions_variant_id", "funnel_sessions", ["variant_id"])
    op.create_index("ix_funnel_sessions_user_id", "funnel_sessions", ["user_id"])

    op.create_table(
        "funnel_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, sa.ForeignKey("funnel_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("funnel_id", UUID, sa.ForeignKey("funnels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", UUID, sa.ForeignKey("funnel_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("step_position", sa.Integer()),
        sa.Column("occurred_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_funnel_events_funnel_occurred", "funnel_events", ["funnel_id", "occurred_at"])
    op.create_index("ix_funnel_events_session_id", "funnel_events", ["session_id"])
    op.create_index(
        "uq_funnel_events_step_view", "funnel_events", ["session_id", "step_position"],
        unique=True, postgresql_where=sa.text("type = 'step_view'"),
    )


def downgrade() -> None:
    op.drop_table("funnel_events")
    op.drop_table("funnel_sessions")
    op.drop_table("funnel_steps")
    op.drop_table("funnel_variants")
    op.drop_table("funnels")
