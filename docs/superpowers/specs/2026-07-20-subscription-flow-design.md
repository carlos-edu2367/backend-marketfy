# Spec — Fluxo completo de assinatura (Marketfy)

- **Data:** 2026-07-20
- **Branch:** `feature/subscription-flow` (worktrees isolados em `backend/.worktrees/subscription-flow` e `frontend/.worktrees/subscription-flow`)
- **Status:** Aprovado para escrita de plano de implementação
- **Repos afetados:** `marketfy/backend`, `marketfy/frontend`
- **Não modificar:** `billing` (billing core) — apenas consumir

---

## 1. Contexto e problema

Hoje a assinatura do Marketfy só é ativada **manualmente pelo admin** do SaaS
(`SubscriptionService.subscribe_manually`). O fluxo público (`/plans`) apenas
registra "interesse" + WhatsApp e mostra "aguarde ativação". A fase de testes e
validação terminou; precisamos finalizar o fluxo real de contratação, ponta a
ponta, self-service.

### Estado atual (já existe e será reaproveitado)

**Backend:**
- `SubscriptionService` — trial, `initiate_subscription` (recorrente via billing),
  `process_webhook_event`/`_apply_event`, `subscribe_manually`, `reconcile_user`.
- `PlanAccessService` — validação de feature/limite/status a partir de
  `BillingSubscriptionModel` (+ fallback `UserModel`).
- Models `BillingSubscriptionModel`, `BillingEventModel`, `PlanModel`, `UserModel`.
- Router `/api/v1/billing/*`: `GET /subscription`, `POST /subscriptions`,
  `GET /jobs/{id}`, `GET /plans/features`, `POST /webhooks/internal`.
- `BillingCoreClient` — `create_customer`, `create_payment` (checkout /payments),
  `get_job`, `get_payment`, `create_subscription` (POST /v1/subscriptions),
  `get_job_status`. Retorna mocks quando `BILLING_CORE_ENABLED=False`.
- **Créditos fiscais** (`FiscalCreditsService` + `/webhooks/billing-core`) — fluxo
  de checkout `/payments` **completo e em produção**: cria pagamento → poll do job
  (`checkout_url`, `payment_id`) → webhook `CHECKOUT_PAID` → ativa. **Esta é a
  referência mecânica direta para o fluxo de Faturas.**
- Worker ARQ (`worker.py` / `WorkerSettings`) com cron jobs
  (`reset_monthly_fiscal_quotas`, `reconcile_pending_bc_payments`).

**Frontend:**
- `/plans` (`Plans.jsx`) — vitrine + fluxo manual (interesse/WhatsApp).
- `AdminLayout.jsx` — redireciona usuário **expirado** para `/plans`, exceto em
  `/dashboard/settings` e `/dashboard/support`.
- `PlanGuard.jsx` — gate de feature (usa `subscription.features` do backend).
- `AuthContext` — carrega `GET /billing/subscription` globalmente em `subscription`.

### Billing core (comportamento observado — não modificar)

- **Recorrente** `POST /v1/subscriptions`: exige `customer_provider_id` (criado antes
  em `POST /v1/customers` com CPF/CNPJ). Cobrança em **cartão de crédito recorrente**.
  Retorna `job_id` (202); o resultado do job traz `checkout_url` (invoice_url do 1º
  pagamento) + `subscription_id`. Cancelamento: `POST /v1/subscriptions/{id}/cancel`.
- **Checkout avulso** `POST /v1/payments`: sem customer prévio (Asaas coleta dados).
  Retorna `job_id` (202); job traz `checkout_url`, `payment_id`, `payment_status`.
  Soma dos `items` deve igualar `value`; `minutes_to_expire` entre 10 e 1440.
- **Webhook de checkout (payments):** eventos Asaas deduplica­dos; entrega interna
  assinada ao `webhook_link` com `payment_id`, `system_payment_id`, `payment_status`.
  Só `CHECKOUT_PAID`/`CONFIRMED`/`RECEIVED` libera benefício.
- **Webhook interno de assinatura (recorrente):** payload
  `{ event, subscription_id, subscription_expires_at, payment_date }` com
  `event ∈ {PAYMENT_RECEIVED, PAYMENT_REFUNDED, SUBSCRIPTION_INACTIVATED}`.
  **Não tem `event_id` nem `system_sub_id`.** Assinatura HMAC-SHA256/base64 em
  `X-Webhook-Signature-256` sobre o JSON canônico (mesmo esquema já usado).

---

## 2. Objetivo

Fluxo self-service completo: após o trial, o usuário escolhe um plano e um **modo
de cobrança**, paga via billing core e tem o acesso liberado automaticamente por
webhook — sem intervenção do admin. Dois modos:

1. **Cobrança Recorrente** — cartão recorrente via subscription do billing core.
2. **Por pagamento (Faturas)** — faturas por período, pagas via checkout /payments.

---

## 3. Decisões (fechadas com o cliente)

| # | Decisão | Escolha |
|---|---------|---------|
| 1 | Cadência das faturas (modo Por pagamento) | **1 fatura por período** (mensal/semestral/anual); gerada ~5 dias antes do vencimento; pagar estende o acesso pelo período; renovação por faturas sucessivas. |
| 2 | Bloqueio quando o plano de faturas vence | **Trava tudo**, exceto **Faturas, Configurações e Suporte**. |
| 3 | Aba de Faturas aparece para quem | **Só modo "Por pagamento".** Recorrentes são cobrados no cartão pelo Asaas e não veem faturas geradas por nós. |
| 4 | Documento do cliente na cobrança recorrente | **Coletar na contratação** (CPF ou CNPJ), reutilizando `users.asaas_customer_id` se já existir. |
| 5 | Ativação do plano "Por pagamento" | **Paga a 1ª fatura para ativar** (webhook `CHECKOUT_PAID`). |
| 6 | Tolerância após vencimento (faturas) | **3 dias** de grace (`past_due`, ainda operacional) antes de travar (`expired`). |
| 7 | Canal do aviso "fatura disponível" | **Banner in-app + e-mail.** |
| 8 | Infra de e-mail | **Mailgun agora**, portando o padrão do Neectify Food e **reutilizando as mesmas credenciais** (config no `.env` do marketfy). |

---

## 4. Modelo de dados

### 4.1 `BillingSubscriptionModel` (alterar)
Adicionar coluna:
- `billing_mode` (String, `recurring | invoice`, default `recurring`, not null).

Reaproveitar as existentes: `status`, `subscription_type` (período: `monthly |
semiannual | annual | trial`), `expires_at`, `plan_id`, `billing_subscription_id`,
`customer_provider_id`, `idempotency_key`.

Ciclo de `status`: `pending → trialing/active → past_due → expired | canceled | failed`.

### 4.2 `BillingInvoiceModel` (novo) — só modo `invoice`
```
id                UUID  pk
owner_id          UUID  fk users(id)              (index)
subscription_id   UUID  fk billing_subscriptions(id) (index)
plan_id           UUID  fk plans(id)
period_start      DateTime   # início do período de acesso que esta fatura cobre
period_end        DateTime   # fim do período (novo expires_at ao pagar)
due_date          DateTime   # vencimento do pagamento
amount            Numeric(10,2)
status            String     # pending | paid | overdue | canceled
bc_job_id         String  nullable   # job do checkout
bc_payment_id     String  nullable   # payment_id do billing core
checkout_url      String  nullable
idempotency_key   String  unique     # dedupe de criação do checkout
paid_at           DateTime nullable
notified_at       DateTime nullable  # dedupe do aviso "fatura disponível"
created_at        DateTime
updated_at        DateTime
```
Índices: `(owner_id)`, `(subscription_id)`, `(subscription_id, status)`, `(status, due_date)`.

Invariante: **no máximo uma fatura `pending` por assinatura** por vez (a próxima só é
gerada após a atual ser paga/cancelada, respeitando a janela dos 5 dias).

Migração: Alembic (backend usa Alembic + SQLAlchemy).

---

## 5. Fluxo A — Por pagamento (Faturas)

1. Usuário escolhe **plano + período + modo Faturas** em `/plans`.
2. Backend cria `BillingSubscriptionModel(billing_mode=invoice, status=pending)` +
   **Fatura #1** (`period_start=now`, `period_end=now+período`, `due_date=now`,
   `amount=preço do período`).
3. Cria checkout `POST /v1/payments` (`system_payment_id = invoice.id`,
   `webhook_link` = endpoint dedicado de faturas) → poll do job → salva
   `bc_job_id`/`checkout_url`/`bc_payment_id`.
4. Frontend redireciona para `checkout_url`.
5. Webhook `CHECKOUT_PAID` (fatura) → fatura `paid` + `paid_at`; subscription
   `active`; `expires_at = period_end`. **Idempotente** por `bc_payment_id`.
6. **Worker diário `generate_due_invoices`:** para cada assinatura `invoice` ativa
   sem fatura `pending`, quando `expires_at - 5 dias <= hoje`, gera a **próxima**
   fatura (`period_start = expires_at`, `period_end = expires_at + período`,
   `due_date = expires_at`), cria o checkout e dispara o aviso "fatura disponível"
   (banner + e-mail; dedupe por `notified_at`).
7. Pagou antes de `due_date` → `expires_at` estende para o novo `period_end`.
8. Não pagou até `due_date` → `past_due` (3 dias de grace, ainda operacional) →
   após o grace → `expired` (**trava**; só Faturas/Config/Suporte).
9. **`reconcile_pending_invoices`** (cron, padrão do `reconcile_pending_bc_payments`):
   consulta o billing para faturas `pending` com `bc_payment_id` antigas, resolve
   pago/expirado, respeitando rate limit (429 interrompe o lote).

---

## 6. Fluxo B — Cobrança Recorrente

1. Usuário escolhe **plano + período + modo Recorrente** e informa **documento
   (CPF/CNPJ)** no checkout.
2. Backend garante `customer_provider_id`: reutiliza `users.asaas_customer_id` ou
   chama `POST /v1/customers` e persiste.
3. `POST /v1/subscriptions` (value do período, cycle=período, `next_due_date`,
   `expires_at`, `webhook_link` = endpoint interno de subscription) → poll do job →
   salva `billing_subscription_id` + `checkout_url` (1º pagamento) na subscription
   (`billing_mode=recurring`, `status=pending`).
4. Frontend redireciona para `checkout_url` (pagamento da 1ª cobrança).
5. Eventos internos do billing atualizam status/`expires_at` (ver §7.2).
   **Sem geração de faturas locais; sem aba Faturas.**
6. Cancelamento (opcional nesta entrega): `POST /v1/subscriptions/{id}/cancel`.

> Nota: o billing usa **cartão de crédito** para recorrência (billing_type fixo).
> "Cobrança Recorrente" = cartão recorrente.

---

## 7. Webhooks (separação limpa por origem)

### 7.1 Faturas (checkout /payments) — **novo endpoint**
`POST /api/v1/webhooks/billing-invoices` — reaproveita a validação HMAC de
`billing_core_webhooks.py`. Mapeia `system_payment_id → BillingInvoiceModel.id`.
Endpoint **separado** do de créditos fiscais (`/webhooks/billing-core`) para não
misturar o roteamento por `system_payment_id` entre pacotes fiscais e faturas.
- `PAID/CONFIRMED/RECEIVED` → ativa a fatura (idempotente por `bc_payment_id`).
- `OVERDUE/REFUNDED/...` → marca fatura conforme regra.

### 7.2 Recorrente (webhook interno de subscription) — **adaptar existente**
`POST /api/v1/billing/webhooks/internal` hoje espera
`{ event_id, event_type, system_sub_id, subscription_id, status, expires_at }`,
mas o billing envia `{ event, subscription_id, subscription_expires_at, payment_date }`.
**Reconciliação necessária:**
- Aceitar o payload real do billing.
- **Sintetizar `event_id`** para idempotência: `f"{subscription_id}:{event}:{payment_date}"`.
- Localizar a subscription local por `billing_subscription_id == subscription_id`.
- Mapear eventos → status:
  - `PAYMENT_RECEIVED` → `active`, `expires_at = subscription_expires_at`.
  - `PAYMENT_REFUNDED` → política de negócio (marcar evento; manter acesso até `expires_at`).
  - `SUBSCRIPTION_INACTIVATED` → `canceled`.
- Persistir bruto em `BillingEventModel` (auditoria) — como já é feito.

---

## 8. Controle de acesso e bloqueio

Centralizar em `PlanAccessService` o **status efetivo** a partir de
`status + expires_at + grace(3d) + billing_mode`:
- `now <= expires_at` → **operacional**.
- `expires_at < now <= expires_at + 3d` (modo invoice) → **past_due**, ainda
  operacional, com banner urgente "fatura vencida".
- `now > expires_at + 3d` (invoice) ou `canceled/expired` → **bloqueado**;
  liberar apenas **Faturas, Configurações, Suporte**.

`GET /billing/subscription` passa a retornar também: `billing_mode`, `locked`
(bool), `invoice_pending` (bool) e um resumo da fatura pendente
(`{ invoice_id, amount, due_date, checkout_url, status }`).

**Roteamento frontend (`AdminLayout`):**
- Expirado **com fatura pendente** (modo invoice) → **não** fica preso em `/plans`;
  vai para a **aba Faturas** (dentro de Configurações da loja) para pagar.
- Expirado **sem assinatura** ou recorrente cancelado → `/plans` (como hoje).
- Adicionar a rota de Faturas à lista de rotas permitidas quando expirado.

---

## 9. Notificações "Você tem uma fatura disponível para pagamento"

- **Banner in-app:** via `GET /billing/subscription` (`invoice_pending` + resumo) —
  badge/banner no dashboard e destaque na aba Faturas. Sem central de notificações nova.
- **E-mail (Mailgun):** portar `MailgunEmailGateway` (padrão do Neectify Food:
  `httpx`, HTTP Basic `api:key`, form-encoded, `POST /v3/{domain}/messages`) para
  `app/infra/integrations/mailgun.py` (ou `infra/clients/`). Novo template HTML
  "fatura disponível". Settings novas (reutilizar credenciais do Neectify Food):
  `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `MAILGUN_FROM_EMAIL`, `MAILGUN_FROM_NAME`,
  `MAILGUN_API_BASE_URL`. Disparo no `generate_due_invoices`, dedupe por
  `invoice.notified_at`. Falha de e-mail **não** bloqueia a geração da fatura (log +
  segue).

---

## 10. Worker / Jobs (ARQ)

Novos jobs em `app/application/jobs/billing_jobs.py`, registrados no
`WorkerSettings.functions` e `cron_jobs`:
- `generate_due_invoices` — cron diário (ex.: 08:00 UTC). Gera próximas faturas na
  janela de 5 dias, cria checkout e envia aviso (banner via dado + e-mail).
- `reconcile_pending_invoices` — cron a cada ~5 min (padrão do
  `reconcile_pending_bc_payments`) — resolve faturas pendentes contra o billing.

Idempotência: geração de fatura dedupe por `(subscription_id, period_start)` +
`idempotency_key`; ativação dedupe por `bc_payment_id`.

---

## 11. Frontend

- **`/plans` (reformular):** cabeçalho "Seu uso expirou, contrate um plano" quando
  vindo de expiração. Passo 1 escolher plano+período; passo 2 escolher **modo**
  (Recorrente | Por pagamento); modo Recorrente → coletar **documento**. Ambos →
  redirecionam para `checkout_url` do billing. Remove o fluxo manual de WhatsApp
  (admin manual permanece só no painel admin).
- **Aba "Faturas"** (dentro de Configurações da loja) — só para modo invoice.
  Lista faturas (status, valor, vencimento) + botão "Pagar" (abre `checkout_url`).
  **Acessível mesmo com plano vencido.** Retornos de navegador
  (`/billing/success|cancel|expired`) reaproveitam as telas já existentes.
- **Banner de fatura pendente/vencida** no dashboard, alimentado por
  `subscription.invoice_pending` / `locked`.
- **Telas travadas:** quando `locked`, exibir estado de bloqueio com CTA para Faturas
  (em vez de redirect para `/plans`).

Novos endpoints backend para a aba:
- `GET /api/v1/billing/invoices` — lista faturas do owner.
- `GET /api/v1/billing/invoices/{id}` — detalhe/refresh do `checkout_url`.
- `POST /api/v1/billing/subscribe` — inicia contratação (recebe `plan_id`,
  `subscription_type`, `billing_mode`, e no recorrente `document`). Substitui/estende
  o atual `POST /billing/subscriptions`.

---

## 12. Segurança e invariantes

- Billing core só é chamado no backend; API key/URL nunca vão ao frontend.
- Toda liberação de acesso vem de **webhook assinado** (HMAC), nunca de retorno de
  navegador.
- Idempotência: criação (idempotency_key) e ativação (`bc_payment_id` / `event_id`
  sintetizado) sempre dedupli­cadas.
- Validação de plano/feature sempre no backend (`PlanAccessService`); frontend é UX.
- Documento (CPF/CNPJ) tratado como dado sensível: não logar em claro.

---

## 13. Fora de escopo (nesta entrega)

- Troca de plano / upgrade-downgrade proporcional (pro-rata).
- Reembolso self-service.
- Central de notificações genérica (só banner + e-mail de fatura).
- Multi-moeda / impostos sobre a assinatura.
- Cobrança recorrente por boleto/PIX recorrente (billing usa cartão).

---

## 14. Riscos e dependências

- **Mapeamento de eventos recorrentes** (§7.2): o payload do billing difere do
  esperado; precisa de teste de integração com payload real/mocado.
- **Credenciais Mailgun**: dependem de configuração no `.env` do marketfy
  (mesmas do Neectify Food). Sem elas, e-mail desliga com log (não quebra fluxo).
- **`BILLING_CORE_ENABLED`**: cliente tem mocks; testes locais sem billing real.
- **Créditos fiscais** compartilham `BILLING_CORE_WEBHOOK_SECRET` e o cliente —
  não regredir o fluxo fiscal ao separar o webhook de faturas.

---

## 15. Fases sugeridas (para o plano de implementação)

1. **Dados + acesso:** migração (`billing_mode`, `BillingInvoiceModel`), status
   efetivo com grace em `PlanAccessService`, `GET /billing/subscription` estendido.
2. **Faturas (core):** serviço de faturas, `POST /billing/subscribe` (modo invoice),
   webhook `/webhooks/billing-invoices`, endpoints de listagem.
3. **Worker:** `generate_due_invoices` + `reconcile_pending_invoices` + registro no
   worker.
4. **Recorrente:** coleta de documento + customer, `subscribe` (modo recurring),
   adaptação do `/webhooks/internal` ao payload real.
5. **E-mail:** `MailgunEmailGateway` + template + settings + disparo no worker.
6. **Frontend:** `/plans` reformulado, aba Faturas, banners, telas de bloqueio,
   roteamento de expirado→Faturas.
7. **Testes de ponta a ponta** por fase (TDD) + verificação com billing mockado.
