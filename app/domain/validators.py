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
