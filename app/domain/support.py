import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from domain.shared import Entity, BusinessRuleException

# =============================================================================
# ENUMS
# =============================================================================

class TicketStatus(Enum):
    OPEN = "aberto"
    IN_PROGRESS = "em_andamento"
    WAITING_USER = "aguardando_usuario"
    RESOLVED = "resolvido"
    CLOSED = "fechado"

class TicketPriority(Enum):
    LOW = "baixa"
    MEDIUM = "media"
    HIGH = "alta"
    CRITICAL = "critica"

# =============================================================================
# ENTIDADES
# =============================================================================

@dataclass
class TicketMessage(Entity):
    """Uma mensagem dentro do histórico do ticket."""
    ticket_id: uuid.UUID
    sender_id: uuid.UUID # Pode ser User ou Admin
    content: str
    is_internal: bool = False # Se true, usuário final não vê (nota interna)

@dataclass
class Ticket(Entity):
    requester_id: uuid.UUID
    market_id: Optional[uuid.UUID] # Opcional, pois pode ser problema de conta/login
    subject: str
    status: TicketStatus = TicketStatus.OPEN
    priority: TicketPriority = TicketPriority.LOW
    messages: List[TicketMessage] = field(default_factory=list)
    assigned_to_id: Optional[uuid.UUID] = None # Admin responsável

    def add_message(self, sender_id: uuid.UUID, content: str, is_internal: bool = False):
        if self.status == TicketStatus.CLOSED:
             raise BusinessRuleException("Não é possível adicionar mensagens a um ticket fechado.")
        
        message = TicketMessage(
            ticket_id=self.id,
            sender_id=sender_id,
            content=content,
            is_internal=is_internal
        )
        self.messages.append(message)
        self.increment_version()
        return message

    def change_status(self, new_status: TicketStatus, user_id: uuid.UUID):
        if self.status == TicketStatus.CLOSED and new_status != TicketStatus.OPEN:
             # Reabertura é permitida, outras trocas em fechado não.
             pass 
        
        self.status = new_status
        self.increment_version()

    def assign_agent(self, agent_id: uuid.UUID):
        self.assigned_to_id = agent_id
        self.status = TicketStatus.IN_PROGRESS
        self.increment_version()