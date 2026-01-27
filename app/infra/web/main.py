import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError # Importação necessária para capturar erros do SQL
from domain.shared import BusinessRuleException # Import da Exceção de Domínio
from infra.config.settings import get_settings
from infra.config.logger import get_logger

# Import Routers
from infra.web.routers import (
    auth, identity, inventory, sales, 
    finance_support, admin, analytics, fiscal,
    finance_report 
)

settings = get_settings()
logger = get_logger("api")

app = FastAPI(
    title=settings.APP_NAME,
    version="1.2.1",
    description="API SGM Marketfy - Sistema de Gestão Multi-tenant com Relatórios Contábeis"
)

# Segurança: CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MIDDLEWARE DE LOG ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    path = request.url.path
    method = request.method
    
    if path == "/health":
        return await call_next(request)

    logger.info(f"📥 Entrada: {method} {path}")
    
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        
        # Loga sucesso apenas se não for erro de validação (que já logamos no handler)
        if response.status_code < 400:
            logger.info(f"✅ Sucesso ({response.status_code}) após {process_time:.2f}ms | {method} {path}")
        
        return response
    except Exception as e:
        process_time = (time.time() - start_time) * 1000
        # Se a exceção não for tratada pelos handlers abaixo, cai aqui como 500
        logger.error(f"💥 Erro Interno (500) após {process_time:.2f}ms: {str(e)}")
        raise

# --- EXCEPTION HANDLERS ---

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handler padrão para erros HTTP lançados propositalmente no código."""
    logger.warning(f"⚠️ Erro HTTP {exc.status_code} em {request.method} {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )

@app.exception_handler(BusinessRuleException)
async def business_rule_exception_handler(request: Request, exc: BusinessRuleException):
    """
    Captura violações de Regra de Negócio (ex: saldo insuficiente, caixa fechado)
    e retorna 400 Bad Request em vez de 500.
    """
    logger.warning(f"⚠️ Regra de Negócio em {request.method} {request.url.path}: {str(exc)}")
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
    )

@app.exception_handler(IntegrityError)
async def integrity_exception_handler(request: Request, exc: IntegrityError):
    """
    NOVO: Captura erros de integridade do banco (Duplicidade, Chave Estrangeira)
    e retorna mensagens amigáveis em vez de erro de SQL.
    """
    error_msg = str(exc.orig) if exc.orig else str(exc)
    detail = "Erro de integridade de dados."
    status_code = 409 # 409 Conflict é o padrão REST para duplicidade

    # Normaliza para minúsculas para facilitar a busca
    error_msg_lower = error_msg.lower()

    # 1. Tratamento de DUPLICIDADE (Unique Violation)
    if "unique constraint" in error_msg_lower:
        if "email" in error_msg_lower:
            detail = "O e-mail informado já está cadastrado no sistema."
        elif "cpf" in error_msg_lower:
            detail = "Este CPF já está sendo utilizado por outro cadastro."
        elif "document" in error_msg_lower or "cnpj" in error_msg_lower: # 'document' é o campo na tabela markets
            detail = "Este CNPJ ou documento já possui uma loja cadastrada."
        elif "code" in error_msg_lower: # Para código interno de produtos
            detail = "Já existe um produto com este código interno nesta loja."
        else:
            detail = "Um registro com estes dados únicos já existe no sistema."
    
    # 2. Tratamento de CHAVE ESTRANGEIRA (Foreign Key Violation)
    elif "foreign key constraint" in error_msg_lower:
        status_code = 400 # Bad Request
        if "market_id" in error_msg_lower:
            detail = "Operação inválida: A loja referenciada não existe."
        elif "user_id" in error_msg_lower:
            detail = "Operação inválida: O usuário referenciado não existe."
        else:
            detail = "Não é possível realizar esta operação pois o item está vinculado a outros registros (ex: Vendas, Estoque) e não pode ser excluído."

    # Loga o erro técnico para o desenvolvedor, mas retorna o amigável para o front
    logger.warning(f"⛔ Conflito de Banco ({status_code}) em {request.method} {request.url.path}: {detail} | Original: {error_msg}")
    
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
    )

# --- ROTAS REGISTRADAS ---
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(identity.router, prefix="/api/v1/identity", tags=["Identity"])
app.include_router(inventory.router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(sales.router, prefix="/api/v1/sales", tags=["Sales"])
app.include_router(finance_support.router_finance, prefix="/api/v1/finance", tags=["Finance"])
app.include_router(finance_support.router_support, prefix="/api/v1/support", tags=["Support"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(fiscal.router, prefix="/api/v1/fiscal", tags=["Fiscal"])
app.include_router(finance_report.router, prefix="/api/v1/finance-reports", tags=["Finance Reports"])

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "version": "1.2.1"}