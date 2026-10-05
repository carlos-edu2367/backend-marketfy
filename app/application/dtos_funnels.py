"""DTOs dos endpoints de funis de venda."""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class FunnelSessionRequest(BaseModel):
    fsid: Optional[uuid.UUID] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    referrer: Optional[str] = None


class FunnelEventRequest(BaseModel):
    fsid: uuid.UUID
    type: str = Field(max_length=40)
    step_position: Optional[int] = Field(default=None, ge=0)


class FunnelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str
    plan_id: Optional[uuid.UUID] = None


class FunnelUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    slug: Optional[str] = None
    plan_id: Optional[uuid.UUID] = None
    tracking_html: Optional[str] = None


class VariantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    weight: int = Field(ge=0)


class VariantUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    weight: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class StepCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    html: str = ""


class StepUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    html: Optional[str] = None


class StepOrderRequest(BaseModel):
    step_ids: list[uuid.UUID]
