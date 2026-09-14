# Spec — Analytics de produto com PostHog (D9)

- **Data:** 2026-09-14
- **Status:** Aguardando revisão do usuário antes do plano de implementação
- **Repos afetados:** `marketfy/backend` (eventos de negócio), `marketfy/frontend` (funil + consentimento)
- **Decisão de origem:** D9 do plano de conversão original, resolvida em conversa com o usuário em 2026-09-14 — PostHog, frontend + backend.

---

## 1. Contexto e problema

Não existe nenhuma instrumentação de analytics no produto hoje — nem frontend (nenhum `gtag`/`posthog`/pixel), nem backend (nenhum evento de negócio emitido além de `audit_logs`, que é auditoria de segurança, não funil de conversão). Toda decisão sobre o que funciona na landing, onde o funil vaza, se o trial converte, é hoje um chute.

## 2. Objetivo

Instrumentar o funil completo (visita → cadastro → trial → assinatura → pagamento) com PostHog, cobrindo tanto eventos de navegação (frontend) quanto eventos de negócio que acontecem sem o usuário estar com a página aberta (webhooks, jobs) — decisão do usuário: "frontend + backend, visão completa".

## 3. Por que PostHog (decisão já fechada)

- Plano free generoso (1M eventos/mês) — adequado ao estágio atual (poucos usuários).
- Funil, session replay e feature flags no mesmo produto — evita integrar 3 ferramentas separadas mais tarde.
- Self-host disponível se o volume crescer e o custo do cloud parar de compensar — não trava o produto numa ferramenta que exige troca completa depois.

## 4. Privacidade e LGPD

Duas superfícies diferentes, tratadas diferente:

- **Frontend (visitante anônimo):** carrega cookie/identificador antes de qualquer login — precisa de consentimento explícito. Banner de cookies simples na landing e nas páginas públicas (`/`, `/precos`, `/termos`, `/privacidade`); o SDK do PostHog só inicializa depois do aceite. Recusa não bloqueia o uso do site, só não gera eventos.
- **Backend (usuário já autenticado):** eventos de negócio (trial ativado, assinatura criada) são disparados para uma conta que já aceitou os Termos de Uso no cadastro — processamento de dado já coletado para fins internos de operação/melhoria do produto, não seguimento de terceiro em visitante anônimo. Isso deve constar na Política de Privacidade (hoje em rascunho/revisão jurídica, ver `Privacy.jsx`) quando ela for finalizada — **não é responsabilidade desta implementação redigir a cláusula legal**, só apontar a pendência.

`identify()` no frontend só roda **depois do login bem-sucedido** — nunca antes, para não linkar um `distinct_id` anônimo a PII sem uma conta confirmada por trás.

## 5. Design — Backend

### 5.1 Cliente

Novo `app/infra/observability/analytics.py`, seguindo o mesmo padrão de `BillingCoreClient` (`infra/clients/billing_core_client.py`) e `record_audit_event` (`infra/observability/audit.py`):

```python
class PostHogClient:
    """HTTP client para a Capture API do PostHog. Nunca levanta exceção para o
    chamador — falha de rede/PostHog fora do ar não pode derrubar uma request
    de negócio real."""

    def __init__(self):
        self._api_key = settings.POSTHOG_API_KEY or ""
        self._host = settings.POSTHOG_HOST
        self._enabled = settings.ANALYTICS_ENABLED and bool(self._api_key)
        self._timeout = 2.0  # segundos — nunca trava a request principal por muito tempo


async def track_event(distinct_id: str, event: str, properties: dict | None = None) -> None:
    """Dispara um evento de negócio. Sempre seguro de chamar — loga e engole
    qualquer falha (timeout, PostHog fora do ar, chave inválida)."""
    ...
```

`track_event()` é `await`ado no fluxo da request (não é fire-and-forget via `asyncio.create_task`) — mesma escolha de design de `record_audit_event`, com timeout curto (2s) para limitar o pior caso. Trade-off documentado: adiciona até ~2s de latência se o PostHog estiver lento, nunca falha a operação de negócio em si.

### 5.2 Configuração (`infra/config/settings.py`)

Segue o padrão de `BILLING_CORE_ENABLED`:

```python
ANALYTICS_ENABLED: bool = False
POSTHOG_API_KEY: Optional[str] = None
POSTHOG_HOST: str = "https://us.i.posthog.com"
```

Sem validador obrigando `ANALYTICS_ENABLED=True` em produção (diferente de `BILLING_CORE_ENABLED`) — analytics é opcional mesmo em produção; times de dev/staging não precisam de uma chave PostHog real para rodar a aplicação.

### 5.3 Pontos de instrumentação (confirmados no código, 2026-09-14)

| Evento | Onde disparar | Arquivo:função | `distinct_id` | Properties |
|---|---|---|---|---|
| `trial_activated` | Depois de `user_repo.save(user)` | `subscription_service.py: SubscriptionService.activate_trial` | `str(user.id)` | `{plan_name: trial_plan.name}` |
| `subscription_created` | No fim do fluxo de sucesso | `invoice_service.py: InvoiceService.contract` | `str(owner_id)` | `{plan_id: str(plan_id), subscription_type, billing_mode: "invoice"}` |
| `subscription_created` | No fim do fluxo de sucesso | `recurring_service.py: RecurringService.contract` | `str(user.id)` | `{plan_id: str(plan_id), subscription_type, billing_mode: "recurring"}` |
| `invoice_paid` | Depois de `mark_paid` confirmar (rows > 0) | `invoice_service.py: InvoiceService.activate_invoice` | `str(invoice.owner_id)` | `{amount: str(invoice.amount), subscription_type: invoice.subscription_type}` — checar campo exato em `BillingInvoiceModel` no plano |
| `fiscal_credits_purchased` | Depois de confirmar o pacote | `fiscal/fiscal_credits_service.py: FiscalCreditsService.activate_package` | `str(owner_id)` | `{quantity, amount}` — checar campos exatos no plano |

Todas as chamadas ficam **depois** da escrita de sucesso no banco (nunca antes) — um evento de analytics nunca deve ser disparado para uma operação que ainda pode falhar/dar rollback depois.

### 5.4 Testes

`track_event` e `PostHogClient` mockáveis por injeção (os services que passam a chamá-lo recebem um `analytics: Optional[AnalyticsClientProtocol] = None` no construtor, com um cliente real como default — mesmo padrão usado pros repositórios). Testes verificam que o evento certo é chamado com os `properties` certos, sem precisar de rede (client fake no teste, sem PostHog real, sem `httpx` mockado com respx nesse nível). Um teste separado, marcado `@pytest.mark.respx` (já usado em `test_mercadopago_client_oauth.py`), cobre o `PostHogClient` real fazendo POST pra API mockada via `respx`.

## 6. Design — Frontend

### 6.1 SDK e inicialização

- `npm install posthog-js`.
- `lib/analytics.js`: wrapper fino — `initAnalytics()`, `track(event, properties)`, `identifyUser(user)` — nunca importa `posthog-js` diretamente nas páginas (mesma razão de existir `lib/api.js` em vez de `axios` espalhado).
- `initAnalytics()` só roda depois do consentimento (ver 6.2); antes disso, `track()`/`identifyUser()` são no-op silencioso (não lança erro, só não faz nada — página funciona igual sem consentimento).

### 6.2 Consentimento

Novo `components/CookieConsentBanner.jsx`: banner simples (aceitar/recusar), fixado no rodapé nas páginas públicas (`Home.jsx`, `Pricing.jsx`, `Terms.jsx`, `Privacy.jsx`). Escolha salva em `localStorage` (`marketfy_cookie_consent: "accepted" | "declined"`). Recarrega a escolha no load de `App.jsx`; se `"accepted"`, chama `initAnalytics()`.

### 6.3 Eventos de funil

| Evento | Onde | Properties |
|---|---|---|
| `landing_viewed` | `Home.jsx`, no mount | — |
| `plan_cta_clicked` | `Home.jsx`/`Pricing.jsx`, clique no CTA de um card de plano | `{plan_id, cycle}` |
| `register_submitted` | `Register.jsx`, depois de `registerUser` ter sucesso | `{has_plan_intent: Boolean(searchParams.get('plan'))}` |
| `trial_activated` | `Register.jsx` e `Plans.jsx`, depois de `POST /auth/trial` ter sucesso | — (o evento de negócio equivalente já sai do backend; esse é o sinal de que a UI confirmou pro usuário) |
| `checkout_modal_opened` | `Plans.jsx: handleSelectPlan` | `{plan_id}` |
| `checkout_started` | `Plans.jsx: handleContract`, antes do `subscribePlan` | `{plan_id, billing_mode, subscription_type}` |
| `checkout_completed` | `Plans.jsx: handleContract`, depois de redirecionar pro pagamento ou pra aba Faturas | `{plan_id, billing_mode, outcome: "redirected_to_payment" \| "invoice_pending"}` |

### 6.4 Identificação

`AuthContext.jsx`, depois de `GET /auth/me` retornar com sucesso (login e refresh de sessão): `identifyUser({id, email, plan_name})` → internamente `posthog.identify(user.id, {email, plan_name})`. Nunca antes disso.

### 6.5 Variáveis de ambiente

`.env.example`:
```
VITE_POSTHOG_KEY=
VITE_POSTHOG_HOST=https://us.i.posthog.com
```

### 6.6 Testes

`lib/analytics.js` mockado nos testes existentes (`vi.mock('../lib/analytics', ...)`) — cada teste de página que dispara um evento verifica a chamada, sem precisar de rede real (mesmo padrão já usado pra `vi.mock('../lib/api', ...)`).

## 7. Fora de escopo

- Dashboards/funis dentro do PostHog em si — configuração de produto, não código.
- A/B testing e feature flags do PostHog — a ferramenta suporta, mas nada nesta rodada usa isso; fica disponível pra depois.
- Instrumentar o app PDV interno (uso do produto pós-login, fora do funil de conversão) — fora do escopo desta rodada, que é sobre conversão, não sobre uso do produto.
- Session replay — vem de graça com o PostHog, mas ligar/desligar é uma configuração de produto no próprio painel, não uma decisão de código.

## 8. Riscos

- Timeout de 2s no `track_event` significa que, no pior caso (PostHog lento), toda operação de negócio instrumentada fica ~2s mais lenta. Aceitável dado o volume esperado (poucos usuários); reavaliar para fire-and-forget de verdade (`BackgroundTasks`) se o volume crescer e a latência incomodar.
- Nenhuma migration de banco — analytics não persiste nada localmente, só envia pro PostHog.
