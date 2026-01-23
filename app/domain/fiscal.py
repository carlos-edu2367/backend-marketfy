import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from datetime import datetime
from domain.shared import Entity, BusinessRuleException

# =============================================================================
# ENUMS
# =============================================================================

class FiscalEnvironment(Enum):
    HOMOLOGATION = "homologacao" # Testes
    PRODUCTION = "producao"      # Validade Jurídica

class InvoiceStatus(Enum):
    PENDING = "pendente"
    PROCESSING = "processando"
    AUTHORIZED = "autorizada"
    REJECTED = "rejeitada"
    CANCELED = "cancelada"
    ERROR = "erro"

# =============================================================================
# ENTIDADES
# =============================================================================

@dataclass
class FiscalConfig(Entity):
    """
    Configurações para emissão de NFC-e da loja.
    Armazena dados sensíveis do certificado e tokens.
    """
    market_id: uuid.UUID
    certificate_path: str # Caminho do arquivo .pfx no storage seguro
    certificate_password: str # Idealmente criptografado
    csc_token: str # Código de Segurança do Contribuinte
    csc_id: str # ID do Token (ex: 000001)
    environment: FiscalEnvironment = FiscalEnvironment.HOMOLOGATION
    
    # Configurações tributárias padrão
    default_ncm: Optional[str] = None
    default_cfop: str = "5102" # Venda de mercadoria
    
    def validate_for_emission(self):
        if not self.certificate_path or not self.certificate_password:
            raise BusinessRuleException("Certificado Digital não configurado.")
        if not self.csc_token or not self.csc_id:
            raise BusinessRuleException("CSC (Token SEFAZ) não configurado.")

@dataclass
class Invoice(Entity):
    """
    Representa uma Nota Fiscal (NFC-e) emitida ou tentada.
    """
    market_id: uuid.UUID
    sale_id: uuid.UUID
    status: InvoiceStatus = InvoiceStatus.PENDING
    
    # Dados de Retorno da SEFAZ/API
    access_key: Optional[str] = None # Chave de acesso de 44 dígitos
    protocol: Optional[str] = None
    xml_url: Optional[str] = None
    pdf_url: Optional[str] = None
    error_message: Optional[str] = None
    
    series: int = 1
    number: Optional[int] = None
    
    emitted_at: Optional[datetime] = None

    def set_authorized(self, key: str, protocol: str, number: int, xml: str, pdf: str):
        self.status = InvoiceStatus.AUTHORIZED
        self.access_key = key
        self.protocol = protocol
        self.number = number
        self.xml_url = xml
        self.pdf_url = pdf
        self.emitted_at = datetime.utcnow()
        self.update_timestamp()

    def set_error(self, message: str):
        self.status = InvoiceStatus.REJECTED
        self.error_message = message
        self.update_timestamp()