from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from pydantic import ValidationError


def test_event_dto_rejects_invalid_funnel_variant():
    from application.dtos import MarketingFunnelEventCreateDTO

    with pytest.raises(ValidationError):
        MarketingFunnelEventCreateDTO(
            visitor_id="v" * 10, funnel_variant="C", event_name="marketfy_funnel_start"
        )


def test_event_dto_accepts_valid_payload():
    from application.dtos import MarketingFunnelEventCreateDTO

    dto = MarketingFunnelEventCreateDTO(
        visitor_id="v" * 10,
        funnel_variant="A",
        event_name="marketfy_funnel_step_view",
        step="q1",
        properties={"answer": "Sim"},
    )
    assert dto.funnel_variant == "A"
    assert dto.step == "q1"


def test_lead_dto_requires_phone_or_email():
    from application.dtos import MarketingFunnelLeadCreateDTO

    with pytest.raises(ValidationError):
        MarketingFunnelLeadCreateDTO(
            visitor_id="v" * 10, funnel_variant="A", name="Ana", answers={},
        )


def test_lead_dto_accepts_phone_only():
    from application.dtos import MarketingFunnelLeadCreateDTO

    dto = MarketingFunnelLeadCreateDTO(
        visitor_id="v" * 10, funnel_variant="A", name="Ana", phone="11999990000", answers={},
    )
    assert dto.phone == "11999990000"
    assert dto.email is None
