import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.exc import IntegrityError

from domain.fiscal import FiscalAuthError, FiscalValidationError
from domain.shared import BusinessRuleException
from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.observability.errors import error_response
from infra.observability.health import readiness_payload
from infra.observability.metrics import metrics_registry
from infra.observability.request_context import (
    ensure_request_id,
    get_request_id,
    reset_request_id,
    set_request_id,
)
from infra.observability.sanitization import sanitize_log_data
from infra.security.rate_limiter import enforce_rate_limit_async

from infra.web.routers import (
    admin,
    admin_fiscal,
    analytics,
    auth,
    billing,
    finance_report,
    finance_support,
    fiscal,
    fiscal_tax_rules,
    fiscal_credits,
    fiscal_webhooks,
    identity,
    inventory,
    billing_core_webhooks,
    billing_invoice_webhooks,
    sales,
)

settings = get_settings()
logger = get_logger("api")

app = FastAPI(
    title=settings.APP_NAME,
    version="1.2.1",
    description="API SGM Marketfy - Sistema de Gestao Multi-tenant com Relatorios Contabeis",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _rate_limit_for_request(request: Request):
    if not settings.RATE_LIMIT_ENABLED:
        return

    method = request.method.upper()
    path = request.url.path

    try:
        if method == "POST" and path == "/api/v1/auth/token":
            await enforce_rate_limit_async(request, "auth-token", limit=10, window_seconds=60)
        elif method == "POST" and path == "/api/v1/auth/trial":
            await enforce_rate_limit_async(request, "auth-trial", limit=5, window_seconds=300)
        elif method == "POST" and path.endswith("/sync") and path.startswith("/api/v1/sales/"):
            await enforce_rate_limit_async(request, "sales-sync", limit=60, window_seconds=60)
        elif path.startswith("/api/v1/admin"):
            await enforce_rate_limit_async(request, "admin", limit=120, window_seconds=60)
        elif method == "POST" and path == "/api/v1/billing/webhooks/internal":
            await enforce_rate_limit_async(request, "billing-webhook", limit=200, window_seconds=60)
        elif method == "POST" and path == "/api/v1/fiscal/webhooks/focus-nfe":
            await enforce_rate_limit_async(request, "fiscal-webhook-focus", limit=500, window_seconds=60)
        elif method == "POST" and path == "/api/v1/fiscal/webhooks/neectify":
            await enforce_rate_limit_async(request, "fiscal-webhook-neectify", limit=500, window_seconds=60)
        elif method == "POST" and path == "/api/v1/webhooks/billing-core":
            await enforce_rate_limit_async(request, "billing-core-webhook", limit=200, window_seconds=60)
        elif method == "POST" and path == "/api/v1/webhooks/billing-invoices":
            await enforce_rate_limit_async(request, "billing-invoices-webhook", limit=200, window_seconds=60)
    except HTTPException as exc:
        if exc.status_code == 429:
            metrics_registry.record_rate_limit(path)
        raise


def _apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    )
    return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    start_time = time.time()
    request_id = ensure_request_id(request.headers.get("X-Request-ID"))
    token = set_request_id(request_id)

    try:
        await _rate_limit_for_request(request)
        response = await call_next(request)
        _apply_security_headers(response)
        response.headers["X-Request-ID"] = request_id
        duration_ms = (time.time() - start_time) * 1000
        route = _route_template(request)
        metrics_registry.record_request(request.method, route, response.status_code, duration_ms)
        logger.info(
            "http_request",
            extra={
                "extra_data": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "route": route,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                }
            },
        )
        return response
    except HTTPException:
        duration_ms = (time.time() - start_time) * 1000
        route = _route_template(request)
        metrics_registry.record_request(request.method, route, 429, duration_ms)
        raise
    except Exception:
        duration_ms = (time.time() - start_time) * 1000
        metrics_registry.record_request(request.method, request.url.path, 500, duration_ms)
        logger.exception(
            "http_unhandled_exception",
            extra={
                "extra_data": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                }
            },
        )
        raise
    finally:
        reset_request_id(token)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(
        "http_exception",
        extra={
            "extra_data": {
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "detail": sanitize_log_data({"detail": exc.detail}),
            }
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            "http_error",
            exc.detail if isinstance(exc.detail, str) else "Erro na requisicao.",
            get_request_id(),
            None if isinstance(exc.detail, str) else sanitize_log_data(exc.detail),
        ),
        headers={"X-Request-ID": get_request_id() or ""},
    )


@app.exception_handler(BusinessRuleException)
async def business_rule_exception_handler(request: Request, exc: BusinessRuleException):
    logger.warning(
        "business_rule_exception",
        extra={"extra_data": {"method": request.method, "path": request.url.path, "status_code": 400}},
    )
    return JSONResponse(
        status_code=400,
        content=error_response(
            getattr(exc, "code", "business_rule"),
            str(exc),
            get_request_id(),
            exc.details() if hasattr(exc, "details") else None,
        ),
        headers={"X-Request-ID": get_request_id() or ""},
    )


@app.exception_handler(FiscalAuthError)
async def fiscal_auth_exception_handler(request: Request, exc: FiscalAuthError):
    logger.warning(
        "fiscal_provider_auth_error",
        extra={
            "extra_data": {
                "method": request.method,
                "path": request.url.path,
                "status_code": 502,
                "detail": sanitize_log_data({"detail": str(exc)}),
            }
        },
    )
    return JSONResponse(
        status_code=502,
        content=error_response(
            "fiscal_provider_auth_error",
            "Falha de autenticacao com o provedor fiscal.",
            get_request_id(),
        ),
        headers={"X-Request-ID": get_request_id() or ""},
    )


@app.exception_handler(FiscalValidationError)
async def fiscal_validation_exception_handler(request: Request, exc: FiscalValidationError):
    logger.warning(
        "fiscal_provider_validation_error",
        extra={
            "extra_data": {
                "method": request.method,
                "path": request.url.path,
                "status_code": 422,
                "detail": sanitize_log_data(exc.details),
            }
        },
    )
    details = exc.details.get("detail") if isinstance(exc.details, dict) and "detail" in exc.details else exc.details
    if not details:
        details = [{"loc": ["body"], "msg": str(exc), "type": "value_error"}]
        
    return JSONResponse(
        status_code=422,
        content=error_response(
            "validation_error",
            "Dados invalidos no provedor fiscal.",
            get_request_id(),
            details,
        ),
        headers={"X-Request-ID": get_request_id() or ""},
    )


@app.exception_handler(IntegrityError)
async def integrity_exception_handler(request: Request, exc: IntegrityError):
    error_msg = str(exc.orig) if exc.orig else str(exc)
    detail = "Erro de integridade de dados."
    status_code = 409
    error_msg_lower = error_msg.lower()

    if "unique constraint" in error_msg_lower:
        if "email" in error_msg_lower:
            detail = "O e-mail informado ja esta cadastrado no sistema."
        elif "cpf" in error_msg_lower:
            detail = "Este CPF ja esta sendo utilizado por outro cadastro."
        elif "document" in error_msg_lower or "cnpj" in error_msg_lower:
            detail = "Este CNPJ ou documento ja possui uma loja cadastrada."
        elif "code" in error_msg_lower:
            detail = "Ja existe um produto com este codigo interno nesta loja."
        else:
            detail = "Um registro com estes dados unicos ja existe no sistema."
    elif "foreign key constraint" in error_msg_lower:
        status_code = 400
        if "market_id" in error_msg_lower:
            detail = "Operacao invalida: a loja referenciada nao existe."
        elif "user_id" in error_msg_lower:
            detail = "Operacao invalida: o usuario referenciado nao existe."
        else:
            detail = "Nao e possivel realizar esta operacao pois o item esta vinculado a outros registros."

    logger.warning(
        "integrity_error",
        extra={"extra_data": {"method": request.method, "path": request.url.path, "status_code": status_code}},
    )
    return JSONResponse(
        status_code=status_code,
        content=error_response("integrity_error", detail, get_request_id()),
        headers={"X-Request-ID": get_request_id() or ""},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = [
        {"loc": error.get("loc"), "msg": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    logger.warning(
        "validation_error",
        extra={"extra_data": {"method": request.method, "path": request.url.path, "status_code": 422}},
    )
    return JSONResponse(
        status_code=422,
        content=error_response("validation_error", "Dados invalidos.", get_request_id(), details),
        headers={"X-Request-ID": get_request_id() or ""},
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "unexpected_exception",
        extra={"extra_data": {"method": request.method, "path": request.url.path, "status_code": 500}},
    )
    return JSONResponse(
        status_code=500,
        content=error_response(
            "internal_error",
            "Erro interno. Informe o request_id ao suporte.",
            get_request_id(),
        ),
        headers={"X-Request-ID": get_request_id() or ""},
    )


app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(identity.router, prefix="/api/v1/identity", tags=["Identity"])
app.include_router(inventory.router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(sales.router, prefix="/api/v1/sales", tags=["Sales"])
app.include_router(finance_support.router_finance, prefix="/api/v1/finance", tags=["Finance"])
app.include_router(finance_support.router_support, prefix="/api/v1/support", tags=["Support"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(admin_fiscal.router, prefix="/api/v1/admin", tags=["Admin Fiscal"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(fiscal_credits.router, prefix="/api/v1/fiscal", tags=["Fiscal Credits"])
app.include_router(fiscal.router, prefix="/api/v1/fiscal", tags=["Fiscal"])
app.include_router(fiscal_tax_rules.router, prefix="/api/v1/fiscal", tags=["Fiscal"])
app.include_router(fiscal_webhooks.router, prefix="/api/v1/fiscal/webhooks", tags=["Fiscal Webhooks"])
app.include_router(billing_core_webhooks.router, prefix="/api/v1/webhooks", tags=["Billing Core Webhooks"])
app.include_router(billing_invoice_webhooks.router, prefix="/api/v1/webhooks", tags=["Billing Invoice Webhooks"])
app.include_router(finance_report.router, prefix="/api/v1/finance-reports", tags=["Finance Reports"])
app.include_router(billing.router, prefix="/api/v1/billing", tags=["Billing"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "version": "1.2.1"}


@app.get("/health/live", tags=["Health"])
async def liveness_check():
    return {"status": "ok", "version": "1.2.1"}


@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    payload, status_code = await readiness_payload()
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/metrics", tags=["Observability"])
async def metrics(request: Request):
    if settings.is_production:
        token = request.headers.get("X-Metrics-Token")
        auth = request.headers.get("Authorization", "")
        bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None
        if token != settings.METRICS_ACCESS_TOKEN and bearer != settings.METRICS_ACCESS_TOKEN:
            raise HTTPException(status_code=404, detail="Nao encontrado.")
    return PlainTextResponse(metrics_registry.to_prometheus_text(), media_type="text/plain")


@app.on_event("startup")
async def startup_event():
    try:
        from infra.queues.arq_config import get_arq_pool
        app.state.arq_pool = await get_arq_pool()
        logger.info("ARQ pool initialized and stored in app.state.arq_pool")
    except Exception as exc:
        logger.error("Failed to initialize ARQ pool on startup", exc_info=exc)


@app.on_event("shutdown")
async def shutdown_event():
    if hasattr(app.state, "arq_pool") and app.state.arq_pool:
        try:
            await app.state.arq_pool.close()
            logger.info("ARQ pool closed successfully")
        except Exception as exc:
            logger.error("Failed to close ARQ pool on shutdown", exc_info=exc)

