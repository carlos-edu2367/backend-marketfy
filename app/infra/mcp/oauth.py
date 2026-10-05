"""OAuth 2.1 mínimo para o MCP: metadados, registro dinâmico (DCR), login de admin com PKCE e tokens.

Tudo é stateless: client_id, código de autorização e tokens são JWTs assinados com SECRET_KEY, cada um
com seu `typ` (mcp_client, mcp_code, mcp_access, mcp_refresh). Tokens do app (`typ=access`) não valem
no MCP e tokens do MCP não valem na API, porque `get_current_user` só aceita `typ=access`.
"""
from __future__ import annotations

import base64
import hashlib
import html
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.audit_service import AuditService
from infra.config.settings import get_settings
from infra.database.setup import get_db
from infra.observability.audit import record_audit_event
from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository
from infra.security.auth_handler import AuthHandler
from infra.security.authorization import is_admin_user
from infra.security.rate_limiter import enforce_rate_limit_async

router = APIRouter()

MCP_PATH = "/mcp"
CODE_TTL_SECONDS = 300
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

# Códigos já trocados (melhor esforço, por processo). O PKCE já impede uso por quem não tem o verifier.
_used_codes: dict[str, float] = {}


# -- URLs -----------------------------------------------------------------------
def public_base_url(request: Request) -> str:
    settings = get_settings()
    if settings.MCP_PUBLIC_BASE_URL:
        return settings.MCP_PUBLIC_BASE_URL.rstrip("/")
    if settings.PUBLIC_API_BASE_URL:
        return settings.PUBLIC_API_BASE_URL.rstrip("/").removesuffix(settings.API_V1_STR.rstrip("/"))
    return str(request.base_url).rstrip("/")


def resource_metadata_url(request: Request) -> str:
    return f"{public_base_url(request)}/.well-known/oauth-protected-resource"


# -- JWT ------------------------------------------------------------------------
def _encode(payload: dict) -> str:
    settings = get_settings()
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _decode(token: Optional[str], typ: str) -> Optional[dict]:
    if not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
    return payload if payload.get("typ") == typ else None


def _exp(delta: timedelta) -> int:
    return int((datetime.now(timezone.utc) + delta).timestamp())


def decode_access_token(token: Optional[str]) -> Optional[dict]:
    return _decode(token, "mcp_access")


def _issue_tokens(user_id: str, client_id_hash: str) -> dict:
    settings = get_settings()
    access_ttl = timedelta(minutes=settings.MCP_ACCESS_TOKEN_EXPIRE_MINUTES)
    access = _encode({"typ": "mcp_access", "sub": user_id, "cid": client_id_hash, "exp": _exp(access_ttl)})
    refresh = _encode({"typ": "mcp_refresh", "sub": user_id, "cid": client_id_hash, "jti": str(uuid.uuid4()),
                       "exp": _exp(timedelta(days=settings.MCP_REFRESH_TOKEN_EXPIRE_DAYS))})
    return {"access_token": access, "token_type": "Bearer", "expires_in": int(access_ttl.total_seconds()),
            "refresh_token": refresh, "scope": "funnels"}


def _client_hash(client_id: str) -> str:
    return hashlib.sha256(client_id.encode()).hexdigest()[:32]


def _pkce_ok(verifier: str, challenge: str) -> bool:
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(digest, challenge)


def _redirect_uri_allowed(uri: str) -> bool:
    parsed = urlparse(uri)
    if parsed.fragment or not parsed.netloc:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status_code, headers=_NO_STORE)


async def _active_admin(db: AsyncSession, user_id: str):
    try:
        user = await SQLAlchemyUserRepository(db).get_by_id(uuid.UUID(user_id))
    except ValueError:
        return None
    if user is None or not getattr(user, "is_active", True) or not is_admin_user(user):
        return None
    return user


async def authenticated_admin(request: Request, db: AsyncSession):
    """Admin dono do Bearer token do MCP, ou None."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else None
    payload = decode_access_token(token)
    if payload is None:
        return None
    return await _active_admin(db, payload.get("sub", ""))


# -- Metadados ------------------------------------------------------------------
@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata(request: Request):
    base = public_base_url(request)
    return {"resource": f"{base}{MCP_PATH}", "authorization_servers": [base],
            "bearer_methods_supported": ["header"], "scopes_supported": ["funnels"],
            "resource_name": "Marketfy — funis de venda"}


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
async def authorization_server_metadata(request: Request):
    base = public_base_url(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["funnels"],
    }


# -- Registro dinâmico de cliente (RFC 7591) ---------------------------------------
@router.post("/oauth/register", status_code=201)
async def register_client(request: Request):
    await enforce_rate_limit_async(request, bucket="mcp_oauth_register", limit=20, window_seconds=3600)
    try:
        body = await request.json()
    except ValueError:
        return _oauth_error("invalid_client_metadata", "Corpo JSON inválido.")
    redirect_uris = body.get("redirect_uris") if isinstance(body, dict) else None
    if (not isinstance(redirect_uris, list) or not redirect_uris or len(redirect_uris) > 10
            or not all(isinstance(u, str) and _redirect_uri_allowed(u) for u in redirect_uris)):
        return _oauth_error("invalid_redirect_uri", "redirect_uris deve ter URLs https (ou http em localhost).")
    client_name = str(body.get("client_name") or "Cliente MCP")[:80]
    client_id = _encode({"typ": "mcp_client", "ru": redirect_uris, "nm": client_name})
    return JSONResponse(status_code=201, headers=_NO_STORE, content={
        "client_id": client_id, "client_id_issued_at": int(time.time()), "client_name": client_name,
        "redirect_uris": redirect_uris, "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
    })


# -- Autorização (tela de login do admin) -------------------------------------------
_AUTH_FIELDS = ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "response_type",
                "scope", "resource")


def _validate_authorize(params: dict) -> tuple[Optional[dict], Optional[str]]:
    client = _decode(params.get("client_id"), "mcp_client")
    if client is None:
        return None, "Cliente OAuth desconhecido. Remova e adicione o conector de novo."
    if params.get("redirect_uri") not in client.get("ru", []):
        return None, "redirect_uri não registrado para este cliente."
    if params.get("response_type") != "code":
        return None, "response_type deve ser 'code'."
    if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
        return None, "PKCE (S256) é obrigatório."
    return client, None


def _login_page(params: dict, client: Optional[dict], error: Optional[str], status_code: int = 200) -> HTMLResponse:
    esc = html.escape
    hidden = "".join(f'<input type="hidden" name="{f}" value="{esc(params.get(f) or "")}">' for f in _AUTH_FIELDS)
    error_html = f'<p class="err">{esc(error)}</p>' if error else ""
    form = ""
    if client is not None:
        host = urlparse(params["redirect_uri"]).netloc
        form = f"""
<p><b>{esc(client.get("nm", "Cliente MCP"))}</b> quer acessar os <b>funis de venda</b> do Marketfy
(criar funis e ler métricas). Após o login você volta para <b>{esc(host)}</b>.</p>
<form method="post">{hidden}
<label>E-mail<input name="email" type="email" autocomplete="username" required></label>
<label>Senha<input name="password" type="password" autocomplete="current-password" required></label>
<button type="submit">Autorizar</button>
</form>
<p class="hint">Somente contas de administrador.</p>"""
    page = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Autorizar acesso — Marketfy</title>
<style>body{{font-family:system-ui,sans-serif;background:#f5f6f8;margin:0;padding:16px;color:#1f2937}}
main{{max-width:380px;margin:8vh auto;background:#fff;border-radius:12px;padding:28px;box-shadow:0 2px 12px #0001}}
h1{{font-size:20px;margin:0 0 12px}}label{{display:block;font-size:14px;margin:14px 0 4px}}
input{{display:block;width:100%;box-sizing:border-box;margin-top:4px;padding:10px;border:1px solid #d1d5db;border-radius:8px;font-size:15px}}
button{{margin-top:20px;width:100%;padding:11px;border:0;border-radius:8px;background:#16a34a;color:#fff;font-size:15px;cursor:pointer}}
.err{{background:#fee2e2;color:#991b1b;padding:10px;border-radius:8px;font-size:14px}}.hint{{font-size:12px;color:#6b7280}}</style>
</head><body><main><h1>Marketfy · Conectar ao Claude</h1>{error_html}{form}</main></body></html>"""
    return HTMLResponse(page, status_code=status_code, headers={
        **_NO_STORE, "X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'",
    })


@router.get("/oauth/authorize")
async def authorize_form(request: Request):
    params = {f: request.query_params.get(f) for f in _AUTH_FIELDS}
    client, error = _validate_authorize(params)
    return _login_page(params, client, error, 400 if error else 200)


@router.post("/oauth/authorize")
async def authorize_submit(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    params = {f: form.get(f) for f in _AUTH_FIELDS}
    client, error = _validate_authorize(params)
    if error:
        return _login_page(params, None, error, 400)
    await enforce_rate_limit_async(request, bucket="mcp_oauth_login", limit=10, window_seconds=300)

    user = await SQLAlchemyUserRepository(db).get_by_email(str(form.get("email") or "").strip())
    valid = False
    if user is not None:
        try:
            valid = AuthHandler.verify_password(str(form.get("password") or "")[:72], user.password_hash)
        except Exception:
            valid = False
    audit = AuditService(SQLAlchemyAuditLogRepository(db))
    if not valid or not getattr(user, "is_active", True) or not is_admin_user(user):
        await record_audit_event(audit, request, actor=user if valid else None, action="mcp.authorize_failed",
                                 resource_type="mcp", result="failed",
                                 metadata={"reason": "not_admin" if valid else "invalid_credentials"})
        message = "Esta conta não é de administrador." if valid else "E-mail ou senha incorretos."
        return _login_page(params, client, message, 401)

    code = _encode({"typ": "mcp_code", "sub": str(user.id), "cid": _client_hash(params["client_id"]),
                    "ru": params["redirect_uri"], "cc": params["code_challenge"], "jti": secrets.token_urlsafe(12),
                    "exp": int(time.time()) + CODE_TTL_SECONDS})
    await record_audit_event(audit, request, actor=user, action="mcp.authorized", resource_type="mcp",
                             result="success", metadata={"client_name": client.get("nm")})
    query = {"code": code}
    if params.get("state"):
        query["state"] = params["state"]
    sep = "&" if "?" in params["redirect_uri"] else "?"
    return RedirectResponse(f"{params['redirect_uri']}{sep}{urlencode(query)}", status_code=302)


# -- Token --------------------------------------------------------------------------
def _consume_code(jti: str, exp: int) -> bool:
    now = time.time()
    for key in [k for k, v in _used_codes.items() if v < now]:
        _used_codes.pop(key, None)
    if jti in _used_codes:
        return False
    _used_codes[jti] = exp
    return True


@router.post("/oauth/token")
async def token(request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit_async(request, bucket="mcp_oauth_token", limit=60, window_seconds=60)
    form = await request.form()
    grant_type = form.get("grant_type")
    client_id = str(form.get("client_id") or "")
    if _decode(client_id, "mcp_client") is None:
        return _oauth_error("invalid_client", "client_id inválido.", 401)

    if grant_type == "authorization_code":
        code = _decode(str(form.get("code") or ""), "mcp_code")
        if (code is None or code.get("cid") != _client_hash(client_id)
                or code.get("ru") != form.get("redirect_uri")):
            return _oauth_error("invalid_grant", "Código inválido ou expirado.")
        if not _pkce_ok(str(form.get("code_verifier") or ""), code.get("cc", "")):
            return _oauth_error("invalid_grant", "code_verifier não confere.")
        if not _consume_code(code["jti"], code["exp"]):
            return _oauth_error("invalid_grant", "Código já utilizado.")
        user_id = code["sub"]
    elif grant_type == "refresh_token":
        refresh = _decode(str(form.get("refresh_token") or ""), "mcp_refresh")
        if refresh is None or refresh.get("cid") != _client_hash(client_id):
            return _oauth_error("invalid_grant", "Refresh token inválido ou expirado.")
        user_id = refresh["sub"]
    else:
        return _oauth_error("unsupported_grant_type", "Use authorization_code ou refresh_token.")

    if await _active_admin(db, user_id) is None:
        return _oauth_error("invalid_grant", "Usuário não é mais um administrador ativo.")
    return JSONResponse(_issue_tokens(user_id, _client_hash(client_id)), headers=_NO_STORE)


def unauthorized_response(request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": "invalid_token", "error_description": "Autentique-se como administrador do Marketfy."},
        headers={"WWW-Authenticate": f'Bearer error="invalid_token", '
                                     f'resource_metadata="{resource_metadata_url(request)}"'},
    )

