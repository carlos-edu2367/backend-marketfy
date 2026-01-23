import uuid
from typing import Optional, Callable, List
from domain.identity import User, Market, UserRole, CPF, Email, CNPJ, Plan
from domain.interfaces import UserRepositoryInterface, MarketRepositoryInterface, PlanRepositoryInterface
from domain.shared import BusinessRuleException
from application.dtos import UserCreateDTO, MarketCreateDTO, UserResponseDTO

class IdentityService:
    def __init__(self, 
                 user_repo: UserRepositoryInterface, 
                 market_repo: MarketRepositoryInterface,
                 plan_repo: PlanRepositoryInterface,
                 hasher: Callable[[str], str]): # Injeção da função de Hash
        self.user_repo = user_repo
        self.market_repo = market_repo
        self.plan_repo = plan_repo
        self.hasher = hasher

    async def register_user(self, dto: UserCreateDTO) -> UserResponseDTO:
        existing_user = await self.user_repo.get_by_email(dto.email)
        if existing_user:
            raise BusinessRuleException("Email já cadastrado.")

        # Gera o hash real usando a função injetada (Bcrypt)
        password_hash = self.hasher(dto.password)

        new_user = User(
            name=dto.name,
            email=Email(dto.email),
            cpf=CPF(dto.cpf),
            password_hash=password_hash,
            role=UserRole.OWNER 
        )

        saved_user = await self.user_repo.save(new_user)
        
        return UserResponseDTO(
            id=saved_user.id,
            name=saved_user.name,
            email=saved_user.email.value,
            role=saved_user.role.value
        )

    async def create_market(self, user_id: uuid.UUID, dto: MarketCreateDTO) -> Market:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise BusinessRuleException("Usuário não encontrado.")
            
        if not user.plan_id:
            raise BusinessRuleException("Você precisa contratar um plano antes de criar um mercado.")

        # 1. Busca o plano ativo
        plan = await self.plan_repo.get_by_id(user.plan_id)
        if not plan:
            raise BusinessRuleException("Plano do usuário não encontrado.")

        # 2. Verifica a validade do plano
        user.check_plan_validity()

        # 3. Conta quantos mercados o usuário já tem
        current_markets_count = await self.market_repo.count_by_owner(user.id)

        # 4. Valida o limite
        if plan.is_limit_reached(current_markets_count, 'markets'):
            raise BusinessRuleException(
                f"Limite de mercados atingido para o plano {plan.name}. Máximo: {plan.max_markets}"
            )

        # 5. Cria o mercado
        new_market = Market(
            owner_id=user.id,
            name=dto.name,
            document=CNPJ(dto.document),
            address=dto.address,
            active=True
        )
        
        return await self.market_repo.save(new_market)

    async def get_user_markets(self, user_id: uuid.UUID) -> List[Market]:
        """Lista todos os mercados do usuário."""
        return await self.market_repo.list_by_owner(user_id)