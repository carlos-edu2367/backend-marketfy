# MCP de funis de venda (Claude Code e Claude Web)

O backend expõe um servidor MCP em **`<base>/mcp`** (Streamable HTTP, JSON-RPC sem estado). O login é por OAuth 2.1
com PKCE: na conexão abre uma página do Marketfy onde um **administrador** entra com e-mail e senha.

## Tools

| Tool | O que faz |
|---|---|
| `funnel_create` | Cria um funil em rascunho (variante A) com etapas HTML opcionais e scripts de rastreamento. Não publica. |
| `funnel_list` | Lista os funis com status, URL pública e KPIs de 30 dias (sessões, pagos, conversão). Filtro por status. |
| `funnel_get` | Detalhes por `funnel_id` ou `slug`: plano, variantes A/B e etapas (`include_html` traz o HTML). |
| `funnel_step_metrics` | Funil etapa a etapa (views de cada etapa → conclusão → cadastro → trial → assinatura → pagamento), com % e queda. |
| `funnel_overview` | Resumo, taxas, maior gargalo, comparação A/B, principais UTMs e série diária. |
| `funnel_list_plans` | Planos que podem ser vendidos (para obter o `plan_id`). |

As métricas usam a mesma consulta da tela admin (`application/services/funnel_reporting.py`): coorte por data de
entrada, últimos 30 dias por padrão, sessões de admin excluídas. Filtros: `date_from`, `date_to`, `variant_id`,
`utm_source`, `utm_campaign`.

## Configuração

```
MCP_ENABLED=true
MCP_PUBLIC_BASE_URL=https://api.sgmmarketfy.com   # sem /api/v1; vazio = deriva de PUBLIC_API_BASE_URL
MCP_ACCESS_TOKEN_EXPIRE_MINUTES=60
MCP_REFRESH_TOKEN_EXPIRE_DAYS=30
```

O Claude Web chama o servidor a partir da nuvem da Anthropic, então a URL precisa ser pública e HTTPS.

## Conectar

**Claude Code**

```bash
claude mcp add --transport http marketfy https://api.sgmmarketfy.com/mcp
```

Depois, dentro do Claude Code, rode `/mcp`, escolha `marketfy` → *Authenticate* e faça o login de admin no navegador.
Em dev local: `claude mcp add --transport http marketfy http://localhost:8000/mcp`.

**Claude Web (claude.ai)**

*Settings → Connectors → Add custom connector*, informe `https://api.sgmmarketfy.com/mcp` e clique em *Connect*.
Os campos de OAuth Client ID/Secret ficam vazios (o cliente se registra sozinho via DCR).

## Endpoints

| Rota | Função |
|---|---|
| `POST /mcp` | JSON-RPC: `initialize`, `ping`, `tools/list`, `tools/call`. `GET`/`DELETE` → 405 (sem SSE/sessão). |
| `GET /.well-known/oauth-protected-resource` | Metadados do recurso (RFC 9728). |
| `GET /.well-known/oauth-authorization-server` | Metadados do servidor de autorização (RFC 8414). |
| `POST /oauth/register` | Registro dinâmico de cliente (RFC 7591); só aceita redirect `https` ou `http://localhost`. |
| `GET/POST /oauth/authorize` | Página de login do admin; exige PKCE S256. |
| `POST /oauth/token` | `authorization_code` e `refresh_token`. |

## Segurança

- Client id, código e tokens são JWTs assinados com `SECRET_KEY`, com `typ` próprio: token do app não abre o MCP
  e token do MCP não abre a API. Trocar `SECRET_KEY` revoga todas as conexões.
- A cada troca de token e a cada chamada o usuário é relido: perder o papel `admin` ou ser desativado corta o acesso
  (o access token vigente vale até expirar, no máximo `MCP_ACCESS_TOKEN_EXPIRE_MINUTES`).
- Login com rate limit (10 tentativas / 5 min por IP) e auditoria (`mcp.authorized`, `mcp.authorize_failed`);
  criação de funil auditada como `funnel.created` com `source=mcp`.
- O bloqueio de reuso de código de autorização é por processo (melhor esforço); o PKCE impede o uso sem o verifier.
