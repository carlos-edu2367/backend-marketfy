from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.validators import validate_cpf, validate_document


def test_validate_cpf_accepts_known_valid_cpf():
    assert validate_cpf("11144477735") is True
    assert validate_cpf("111.444.777-35") is True


def test_validate_cpf_rejects_wrong_check_digit():
    assert validate_cpf("11144477734") is False


def test_validate_cpf_rejects_all_same_digits():
    assert validate_cpf("11111111111") is False


def test_validate_cpf_rejects_wrong_length():
    assert validate_cpf("123") is False
    assert validate_cpf("123456789012") is False


def test_validate_document_accepts_valid_cpf_11_digits():
    assert validate_document("11144477735") is True


def test_validate_document_accepts_valid_cnpj_14_digits():
    assert validate_document("12345678000195") is True


def test_validate_document_rejects_wrong_length():
    assert validate_document("123456789012") is False  # 12 dígitos


def test_validate_document_rejects_cpf_with_bad_checksum():
    assert validate_document("11144477734") is False
