"""
Módulo de validação de dados de domínio do Marketfy.
Contém algoritmos de validação matemática estrita.
"""
from __future__ import annotations

import re

def validate_cnpj(cnpj: str) -> bool:
    """
    Executa a validação matemática detalhada de um CNPJ (dígitos verificadores).
    Retorna True se o CNPJ for válido, False caso contrário.
    """
    # Remove qualquer caracter que não seja dígito
    cnpj_digits = re.sub(r"\D", "", cnpj)
    
    if len(cnpj_digits) != 14:
        return False
        
    # CNPJs com todos os dígitos iguais são inválidos
    if len(set(cnpj_digits)) == 1:
        return False
        
    # Primeiro dígito verificador
    weights_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    sum_1 = sum(int(cnpj_digits[i]) * weights_1[i] for i in range(12))
    remainder_1 = sum_1 % 11
    digit_1 = 0 if remainder_1 < 2 else 11 - remainder_1
    
    if int(cnpj_digits[12]) != digit_1:
        return False
        
    # Segundo dígito verificador
    weights_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    sum_2 = sum(int(cnpj_digits[i]) * weights_2[i] for i in range(13))
    remainder_2 = sum_2 % 11
    digit_2 = 0 if remainder_2 < 2 else 11 - remainder_2
    
    if int(cnpj_digits[13]) != digit_2:
        return False

    return True


def validate_cpf(cpf: str) -> bool:
    """
    Executa a validação matemática detalhada de um CPF (dígitos verificadores).
    Retorna True se o CPF for válido, False caso contrário.
    """
    cpf_digits = re.sub(r"\D", "", cpf)

    if len(cpf_digits) != 11:
        return False

    if len(set(cpf_digits)) == 1:
        return False

    weights_1 = [10, 9, 8, 7, 6, 5, 4, 3, 2]
    sum_1 = sum(int(cpf_digits[i]) * weights_1[i] for i in range(9))
    remainder_1 = sum_1 % 11
    digit_1 = 0 if remainder_1 < 2 else 11 - remainder_1

    if int(cpf_digits[9]) != digit_1:
        return False

    weights_2 = [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]
    sum_2 = sum(int(cpf_digits[i]) * weights_2[i] for i in range(10))
    remainder_2 = sum_2 % 11
    digit_2 = 0 if remainder_2 < 2 else 11 - remainder_2

    if int(cpf_digits[10]) != digit_2:
        return False

    return True


def validate_document(digits: str) -> bool:
    """
    Despacha para validate_cpf (11 dígitos) ou validate_cnpj (14 dígitos)
    conforme o tamanho, depois de limpar caracteres não numéricos.
    Retorna False para qualquer outro tamanho.
    """
    clean = re.sub(r"\D", "", digits)
    if len(clean) == 11:
        return validate_cpf(clean)
    if len(clean) == 14:
        return validate_cnpj(clean)
    return False
