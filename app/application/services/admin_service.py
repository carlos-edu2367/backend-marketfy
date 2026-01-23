import uuid
from decimal import Decimal
from typing import List
from domain.identity import Plan, PlanType
from domain.interfaces import PlanRepositoryInterface
from domain.shared import BusinessRuleException
from application.dtos import PlanCreateDTO, PlanUpdateDTO

class AdminService:
    def __init__(self, plan_repo: PlanRepositoryInterface):
        self.plan_repo = plan_repo

    async def create_plan(self, dto: PlanCreateDTO) -> Plan:
        try:
            p_type = PlanType(dto.type)
        except ValueError:
            raise BusinessRuleException(f"Tipo de plano inválido: {dto.type}. Use 'cortesia', 'pago' ou 'trial'.")

        # Regra de Negócio: Se for cortesia, todos os preços devem ser zero
        if p_type == PlanType.FREE:
            price_monthly = Decimal("0.00")
            price_180days = Decimal("0.00")
            price_annual = Decimal("0.00")
        else:
            price_monthly = dto.price_monthly
            price_180days = dto.price_180days
            price_annual = dto.price_annual
            
        plan = Plan(
            name=dto.name,
            type=p_type,
            max_markets=dto.max_markets,
            max_terminals=dto.max_terminals, # CORRIGIDO: Usa max_terminals
            price_monthly=price_monthly,
            price_180days=price_180days,
            price_annual=price_annual,
            is_active=True
        )
        
        return await self.plan_repo.save(plan)

    async def update_plan(self, plan_id: uuid.UUID, dto: PlanUpdateDTO) -> Plan:
        plan = await self.plan_repo.get_by_id(plan_id)
        if not plan:
            raise BusinessRuleException("Plano não encontrado.")

        if dto.name: plan.name = dto.name
        if dto.max_markets is not None: plan.max_markets = dto.max_markets
        if dto.max_terminals is not None: plan.max_terminals = dto.max_terminals # CORRIGIDO
        
        # Se for cortesia, impede atualização de preço para valor > 0
        is_free = plan.type == PlanType.FREE
        
        if dto.price_monthly is not None: 
            plan.price_monthly = Decimal("0.00") if is_free else dto.price_monthly
            
        if dto.price_180days is not None: 
            plan.price_180days = Decimal("0.00") if is_free else dto.price_180days
            
        if dto.price_annual is not None: 
            plan.price_annual = Decimal("0.00") if is_free else dto.price_annual
            
        if dto.is_active is not None:
            plan.is_active = dto.is_active

        return await self.plan_repo.save(plan)

    async def list_plans(self) -> List[Plan]:
        return await self.plan_repo.list_all()