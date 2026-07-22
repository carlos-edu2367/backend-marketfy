"""Persist structured market locations and Mercado Pago Store sync state."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260721_0019"
down_revision: Union[str, Sequence[str], None] = "20260721_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("postal_code", sa.String(length=8), nullable=False),
        sa.Column("street_name", sa.String(length=160), nullable=False),
        sa.Column("street_number", sa.String(length=32), nullable=False),
        sa.Column("district", sa.String(length=120), nullable=True),
        sa.Column("complement", sa.String(length=160), nullable=True),
        sa.Column("city_name", sa.String(length=120), nullable=False),
        sa.Column("state_code", sa.String(length=2), nullable=False),
        sa.Column("state_name", sa.String(length=80), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default="BR"),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("location_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_market_location_latitude"),
        sa.CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_market_location_longitude"),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_id", name="uq_market_locations_market"),
    )
    op.create_index("ix_market_locations_market", "market_locations", ["market_id"])

    op.create_table(
        "mercado_pago_store_registrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mp_user_id", sa.String(), nullable=False),
        sa.Column("mp_store_id", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(length=60), nullable=False),
        sa.Column("location_version_synced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_status", sa.String(length=24), nullable=False, server_default="synced"),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_id", name="uq_mp_store_market"),
    )
    op.create_index("ix_mp_store_market", "mercado_pago_store_registrations", ["market_id"])


def downgrade() -> None:
    op.drop_index("ix_mp_store_market", table_name="mercado_pago_store_registrations")
    op.drop_table("mercado_pago_store_registrations")
    op.drop_index("ix_market_locations_market", table_name="market_locations")
    op.drop_table("market_locations")
