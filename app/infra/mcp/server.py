"""Endpoint MCP em Streamable HTTP sem estado (JSON-RPC 2.0, respostas application/json, sem SSE)."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.funnels import FunnelError
from infra.config.logger import get_logger
from infra.database.setup import get_db
from infra.mcp.funnel_tools import TOOLS, ToolContext
from infra.mcp.oauth import MCP_PATH, authenticated_admin, unauthorized_response

router = APIRouter()
logger = get_logger("mcp")

SERVER_INFO = {"name": "marketfy-funnels", "title": "Marketfy — Funis de venda", "version": "1.0.0"}
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_VERSION = "2025-06-18"
INSTRUCTIONS = (
    "Funis de venda do Marketfy (SaaS de PDV para mercados). Cada funil tem variantes A/B com etapas em HTML "
    "e termina no cadastro/checkout de um plano. Comece por funnel_list para obter o funnel_id. "
    "Para diagnosticar desempenho use funnel_overview e funnel_step_metrics; confira `notes` (amostra pequena, "
    "coorte recente) antes de concluir sobre conversão. funnel_create cria apenas rascunhos; publicar é no painel. "
    "Valores monetários estão em BRL."
)

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def _result(req_id, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code: int, message: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
                        status_code=status_code)


def _tool_text(payload: Any, *, is_error: bool = False) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "Parâmetros inválidos — " + "; ".join(parts)


async def _call_tool(params: dict, ctx: ToolContext) -> dict:
    tool = TOOLS[params["name"]]
    try:
        args = tool.input_model.model_validate(params.get("arguments") or {})
        result = await tool.handler(ctx, args)
    except ValidationError as exc:
        await ctx.db.rollback()
        return _tool_text(_validation_message(exc), is_error=True)
    except FunnelError as exc:
        await ctx.db.rollback()
        detail = {"error": exc.code, "message": exc.message}
        if getattr(exc, "errors", None):
            detail["errors"] = exc.errors
        return _tool_text(detail, is_error=True)
    except ValueError as exc:
        await ctx.db.rollback()
        return _tool_text(str(exc), is_error=True)
    except Exception:
        await ctx.db.rollback()
        logger.exception("Falha na tool MCP %s", tool.name)
        return _tool_text("Erro interno ao executar a ferramenta. Tente novamente.", is_error=True)
    if not tool.read_only:
        await ctx.db.commit()
    return _tool_text(result)


def _negotiate(version: Optional[str]) -> str:
    return version if version in SUPPORTED_VERSIONS else DEFAULT_VERSION


@router.post(MCP_PATH)
async def mcp_post(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await authenticated_admin(request, db)
    if admin is None:
        return unauthorized_response(request)
    try:
        message = await request.json()
    except ValueError:
        return _error(None, PARSE_ERROR, "JSON inválido.", 400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "Esperado um objeto JSON-RPC 2.0 (lotes não são suportados).", 400)
    if "id" not in message or "method" not in message:
        return Response(status_code=202)  # notificação ou resposta do cliente: nada a devolver

    req_id, method = message["id"], message["method"]
    params = message.get("params") or {}
    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": _negotiate(params.get("protocolVersion")),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": [t.definition() for t in TOOLS.values()]})
    if method == "tools/call":
        if not isinstance(params, dict) or params.get("name") not in TOOLS:
            return _error(req_id, INVALID_PARAMS, f"Ferramenta desconhecida: {params.get('name')!r}.")
        return _result(req_id, await _call_tool(params, ToolContext(db=db, admin=admin, request=request)))
    if method in ("resources/list", "prompts/list"):
        return _result(req_id, {method.split("/")[0]: []})
    return _error(req_id, METHOD_NOT_FOUND, f"Método não suportado: {method}.")


@router.get(MCP_PATH)
@router.delete(MCP_PATH)
async def mcp_no_stream():
    # Servidor sem estado: não abre stream SSE nem mantém sessão.
    return Response(status_code=405, headers={"Allow": "POST"})
