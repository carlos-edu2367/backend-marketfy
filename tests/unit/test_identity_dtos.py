from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from pydantic import ValidationError

from application.dtos import UserCreateDTO, MarketCreateDTO


def test_user_create_dto_accepts_missing_cpf():
    dto = UserCreateDTO(name="Ana", email="ana@t.com", password="segredo1")
    assert dto.cpf is None


def test_user_create_dto_still_accepts_cpf_if_sent():
    dto = UserCreateDTO(name="Ana", email="ana@t.com", cpf="11144477735", password="segredo1")
    assert dto.cpf == "11144477735"


def test_market_create_dto_accepts_valid_cpf_as_document():
    dto = MarketCreateDTO(name="Loja", document="111.444.777-35", address="Rua X")
    assert dto.document == "111.444.777-35"


def test_market_create_dto_accepts_valid_cnpj_as_document():
    dto = MarketCreateDTO(name="Loja", document="12.345.678/0001-95", address="Rua X")
    assert dto.document == "12.345.678/0001-95"


def test_market_create_dto_rejects_invalid_document():
    with pytest.raises(ValidationError):
        MarketCreateDTO(name="Loja", document="123", address="Rua X")


def test_market_create_dto_rejects_document_with_bad_checksum():
    with pytest.raises(ValidationError):
        MarketCreateDTO(name="Loja", document="111.444.777-34", address="Rua X")
