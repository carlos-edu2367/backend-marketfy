import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from infra.config.settings import get_settings
from infra.config.logger import get_logger

# Import Routers
from infra.web.routers import (
    auth, identity, inventory, sales, 
    finance_support, admin, analytics, fiscal,
    finance_report # NOVO
)

settings = get_settings()
logger = get_logger("api")

app = FastAPI(
    title=settings.APP_NAME,
    version="1.2.0",
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
        status_code = response.status_code
        
        status_emoji = "✅" if status_code < 400 else "⚠️" if status_code < 500 else "❌"
        logger.info(f"{status_emoji} Saída: {status_code} | {process_time:.2f}ms | {method} {path}")
        
        return response
    except Exception as e:
        process_time = (time.time() - start_time) * 1000
        logger.error(f"💥 Erro Interno (500) após {process_time:.2f}ms: {str(e)}")
        raise

# --- EXCEPTION HANDLERS ---
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(f"⚠️ Erro HTTP {exc.status_code} em {request.method} {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )

# --- ROTAS REGISTRADAS ---
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(identity.router, prefix="/api/v1/identity", tags=["Identity"])
app.include_router(inventory.router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(sales.router, prefix="/api/v1/sales", tags=["Sales"])
app.include_router(finance_support.router_finance, prefix="/api/v1/finance", tags=["Finance"])
app.include_router(finance_support.router_support, prefix="/api/v1/support", tags=["Support"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin SaaS"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(fiscal.router, prefix="/api/v1/fiscal", tags=["Fiscal"])
app.include_router(finance_report.router, prefix="/api/v1/reports", tags=["Reports & Accounting"]) # NOVO