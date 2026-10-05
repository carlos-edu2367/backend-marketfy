# SGM Marketfy — Overview

SaaS multi-tenant de **gestão e PDV (ponto de venda) para mercados/mercearias**. Cobre venda no caixa, estoque, clientes (fiado), financeiro, emissão fiscal (NFC-e), pagamento Pix presencial e cobrança de assinatura da própria plataforma.

O repositório é dividido em dois projetos independentes (cada um com seu próprio `.git`): `backend/` e `frontend/`.

---

## 1. O que o sistema faz

### Para o lojista (dono/gerente/caixa)
| Área | Funcionalidade |
|---|---|
| **PDV** | Tela de caixa por mercado/terminal; abertura e fechamento de caixa (`Box`), sangria/suprimento, múltiplas formas de pagamento, cupom (`Receipt`), seleção de cliente. Funciona **offline-first**: vendas são gravadas localmente (IndexedDB/Dexie) e sincronizadas via `POST /sales/{market}/sync`. |
| **Estoque** | Produtos, movimentações (entrada/saída/ajuste, com preço de custo), histórico por produto, sync incremental de produtos para o PDV. |
| **Clientes** | Cadastro e conta corrente/fiado (`CustomerLedger`), com limite de crédito. |
| **Vendas** | Histórico, cancelamento, dashboard por mercado e resumo diário. |
| **Financeiro** | Transações financeiras, relatórios (exportação XLSX/PDF), recorrências. Liberado conforme o plano (`includes_finance`). |
| **Fiscal (NFC-e)** | Emissão via **Neectify Fiscal**, serviço fiscal próprio (ver seção 3). Onboarding fiscal, configuração por tenant (certificado, CSC), emissão assíncrona de NFC-e, reprocessamento, cancelamento, inutilização, impressão do DANFE, notificações, **regras tributárias por produto** (ICMS/NCM/CFOP etc., com fluxo de aprovação contábil, autorização SEFAZ e histórico de atribuição), snapshots fiscais imutáveis por venda e rollout controlado por flag. |
| **Créditos fiscais** | Compra de pacotes de emissões (via Mercado Pago/Billing Core) além da cota mensal do plano; ledger de uso e contadores. |
| **Pix** | Pix com QR dinâmico presencial via **Mercado Pago**: conexão OAuth por mercado, localização/loja/POS, geração de QR no PDV, status em tempo real por **SSE**, webhooks, reconciliação e detector de anomalias (pago-sem-venda / venda-sem-confirmação). |
| **Assinatura** | Planos (cortesia/pago/trial; mensal, 180 dias, anual), checkout, faturas (`BillingInvoice`), retentativa, paywall por plano (limite de mercados, terminais e cota fiscal). |
| **Suporte** | Tickets com mensagens. |
| **Multi-usuário** | Papéis: `admin`, `owner`, `manager`, `accountant`, `cashier`; membros por mercado (`MarketMember`). |

### Para o operador da plataforma (admin SaaS)
Dashboard de métricas, gestão de planos (preços, ordem, destaque), tickets de suporte, concessão manual de créditos fiscais, administração do Pix e do rollout fiscal e **funis de venda** (ver abaixo).

### Funis de venda (admin)
O admin cria funis para vender os planos do Marketfy e acompanha a conversão da visita até a primeira fatura paga, tudo dentro do sistema (sem depender do PostHog).

- **Páginas por HTML:** cada etapa é HTML livre (CSS/JS inclusive), exibido em `<iframe sandbox>` sem `allow-same-origin`, então não acessa cookies nem tokens do app. A etapa avança com `data-funnel-next` / `data-funnel-back` / `data-funnel-finish` ou `Funnel.next()` (protocolo `postMessage`).
- **URL pública:** `/f/<slug>`. Ao fim, leva ao cadastro (`/register?plan=…&fsid=…`) ou, se logado, a `/plans`, reaproveitando o checkout existente.
- **A/B e origem:** variantes com peso (a sessão fica presa a uma variante), captura de `utm_*` e referrer, scripts de rastreamento (pixels) por funil.
- **Métricas:** por coorte de entrada — etapa a etapa com queda, cadastro → trial → assinatura → pago, receita, comparação A/B (com aviso de amostra pequena), quebra por origem e série diária. Sessões de admin são excluídas.
- **Atribuição:** o `fsid` da sessão é ligado ao usuário no cadastro; trial, assinatura e primeiro pagamento marcam a sessão pelo mesmo ponto que já emite eventos ao PostHog (`FUNNEL_ATTRIBUTION_ENABLED`).
- **Spec e plano:** `backend/docs/superpowers/specs/2026-10-04-sales-funnels-design.md` e `.../plans/2026-10-04-sales-funnels.md`.

### Páginas públicas
Landing, `/precos`, login/registro, termos e privacidade, banner de cookies, analytics de produto (PostHog).

---

## 2. Arquitetura

```
┌──────────────┐   HTTPS/JSON + SSE    ┌───────────────────────┐
│  Frontend    │ ────────────────────▶ │  API FastAPI (/api/v1)│
│ React + Vite │ ◀──────────────────── │  routers → services   │
│ Dexie (offl.)│                       │  → repositories       │
└──────────────┘                       └──────┬───────────┬────┘
                                              │           │ enfileira
                                   PostgreSQL │           ▼
                                   (SQLAlchemy│   ┌──────────────┐
                                    + Alembic)│   │ Redis (ARQ,  │
                                              │   │ locks, SSE,  │
                                              │   │ rate limit)  │
                                              │   └──────┬───────┘
                                              ▼          ▼
                                        ┌────────────────────────┐
                                        │ Worker ARQ (worker.py) │
                                        │ jobs + cron            │
                                        └──────────┬─────────────┘
              Integrações externas: Neectify Fiscal (NFC-e) · Mercado Pago (Pix/OAuth)
              · Billing Core (cobrança) · Mailgun (e-mail) · ViaCEP · S3 (artefatos fiscais)
```

### Backend (`backend/`) — Python 3.11, FastAPI
Arquitetura em camadas (estilo hexagonal/DDD leve), com `PYTHONPATH=/app/app`:

- `app/domain/` — entidades e regras puras (identity, inventory, sales, finance, fiscal, fiscal_tax, pix, market_location, validators CPF/CNPJ/Email, interfaces/ports).
- `app/application/` — `services/` (casos de uso: sales, inventory, identity, subscription, plan_access, recurring, finance_report, analytics, admin…; subpacotes `fiscal/` e `pix/`), `dtos`, e `jobs/` (billing, fiscal, pix).
- `app/infra/` — adaptadores:
  - `web/` (FastAPI `main.py`, `dependencies.py`, `routers/`);
  - `database/` (modelos SQLAlchemy, ~55 tabelas) e `repositories/`;
  - `providers/` (fiscal: Neectify, Focus NFe, fake; pix: Mercado Pago; cep: ViaCEP) com factory;
  - `clients/` (Billing Core, Mercado Pago, Neectify);
  - `security/` (JWT + refresh em cookie, autorização por papel/mercado, rate limiter, cifra de segredos, uploads);
  - `cache/` (Redis, locks, event bus do Pix), `queues/` (ARQ), `storage/` (artefatos fiscais local/S3), `observability/` (logs, métricas, auditoria, health, sanitização, request context).
- `alembic/versions/` — ~45 migrations (inclui duas rodadas de refatoração fiscal e Pix).
- `worker.py` — worker ARQ com filas `fiscal:high` e `pix:high` e crons: reset mensal de cotas fiscais, reconciliação de pagamentos/faturas do Billing Core, geração diária de faturas, reconciliação/expiração de tentativas Pix, renovação de tokens OAuth e scan de anomalias.
- `tests/` — `unit`, `integration` (inclui Postgres e SSE) e `contract` (contrato fiscal v2).
- `docs/` — runbook de rollout Pix/localização, notas de planos/paywall, specs e planos.

**Rotas principais** (`/api/v1`): `auth`, `identity`, `inventory`, `sales`, `finance`, `finance-reports`, `support`, `billing`, `fiscal` (+ `tax-rules`, `credits`), `pix`, `analytics`, `admin` (+ `admin fiscal`, `admin pix`), e webhooks (`fiscal`, `billing-core`, `billing invoice`, `mercado pago`).

### Frontend (`frontend/`) — React 18 + Vite
- Tailwind CSS, react-router v6, react-hook-form + zod, axios, recharts, leaflet (mapa de localização), qrcode, jspdf, react-hot-toast, PostHog.
- **Offline-first** com Dexie (`lib/db.js`, hooks `useSync` / `useProductSync`).
- Rotas: `/` (redirect), `/login`, `/register`, `/precos`, `/plans`, `/dashboard/*` (dashboard, mercado, histórico, financeiro com `PlanGuard`, estoque, clientes, configurações, suporte, créditos fiscais), `/admin/*` e `/pdv/:marketId`.
- Componentes por domínio: `fiscal/` (Fiscal Center, wizard de regras tributárias, onboarding), `pdv/` (pagamento, QR Pix, recibo), `billing/`, `settings/` (Pix, fiscal, mapa), `layout/` (SaaS e Admin).
- Testes com Vitest + Testing Library.

---

## 3. Fiscal próprio — emissão de NFC-e (Neectify Fiscal)

A emissão de NFC-e **não é feita dentro do Marketfy**: ela é delegada a um serviço fiscal próprio da família Neectify, o **Neectify Fiscal**, uma API separada (`NEECTIFY_BASE_URL`) que cuida da parte pesada: geração do XML, assinatura com certificado A1, validação XSD, transmissão à SEFAZ, protocolo de autorização, cancelamento, inutilização e DANFE. O Marketfy é o cliente dessa API (`X-Internal-Client: marketfy`) e orquestra o ciclo de vida da nota a partir da venda.

### Por que existe
- O Marketfy fica focado em PDV/estoque e **não manipula XML nem fala com a SEFAZ**.
- Todos os produtos Neectify compartilham o mesmo motor fiscal.
- O provider é plugável (`FiscalProviderGateway`): `neectify_fiscal` (padrão), `focus_nfe` (legado/alternativo) e `fake` (dev/teste; **bloqueado em produção**), escolhido por `FISCAL_PROVIDER` via `provider_factory`.

### Camadas no Marketfy
| Camada | Arquivo | Papel |
|---|---|---|
| Client HTTP | `infra/clients/neectify_fiscal_client.py` | Sessão httpx com pool, API key em `X-API-Key`, retry com backoff em 429/503/504, API key nunca logada. |
| Provider | `infra/providers/fiscal/neectify_fiscal_provider.py` | Traduz a interface interna para o contrato REST do Neectify; envia `Idempotency-Key` em emissão, cancelamento e inutilização. |
| Contrato v2 | `application/services/fiscal/fiscal_contract_v2.py` | Payload `marketfy.fiscal-tax-snapshot.v2`, montado só a partir da evidência fiscal da venda, com valores decimais exatos e hash SHA-256 canônico. |
| Pré-validação | `fiscal_pre_validator.py` | Valida a venda e monta o payload (mapeia formas de pagamento para códigos fiscais). |
| Emissão | `fiscal_emission_service.py` + `jobs/fiscal_jobs.py` | Cria o `FiscalDocument`, reserva cota e enfileira `emit_nfce_job` no ARQ. |
| Reconciliação | `fiscal_reconciliation_service.py` | Resolve estados incertos consultando o provider, com política de retry. |
| Onboarding | `fiscal_onboarding_service.py` | Cadastra o emitente e sincroniza com o Neectify. |

### Endpoints do Neectify Fiscal consumidos
`POST /v1/issuers` (emitente) · `POST /v1/issuers/{id}/nfce-configs` · `POST /v1/issuers/{id}/certificates` + `POST /v1/certificates/{id}/activate` · `POST /v1/nfce` (emitir) · `GET /v1/nfce/{ref}` (consultar) · `POST /v1/nfce/{ref}/cancel` · `GET /v1/nfce/{ref}/xml` · `GET /v1/nfce/{ref}/danfe` · `POST /v1/nfce/numbering/inutilize`.

### Fluxo de emissão
```
Venda finalizada no PDV
  → POST /fiscal/{market}/sales/{sale}/emit
  → pré-validação + snapshot fiscal imutável (regras tributárias do produto)
  → reserva de cota/crédito fiscal (check_and_reserve)
  → FiscalDocument = queued  →  job emit_nfce_job (ARQ, idempotente)
  → Neectify Fiscal: XML → assinatura → XSD → SEFAZ
  → resultado: authorized | rejected | sefaz_unavailable | contingency_required | provider_error | manual_action_required
  → webhook assinado (HMAC-SHA256 + timestamp, X-Neectify-Signature) e/ou reconciliação por consulta
  → download de XML/DANFE (S3 ou storage local) e consumo/liberação da cota (ledger)
```
Cada chamada ao provider gera um `FiscalAttempt` (emit, consult, cancel, inutilize, download) e cada mudança relevante um `FiscalEvent` (origem: marketfy, provider, webhook, worker, admin), dando trilha de auditoria completa. Há também recibo offline (`offline_receipt_issued`) para quando a SEFAZ está fora, com limite de idade configurável (`FISCAL_OFFLINE_MAX_AGE_MINUTES`).

### Onboarding do emitente (por mercado)
Checklist com percentual de conclusão: **dados fiscais** (CNPJ, IE, regime tributário: Simples, Lucro Presumido/Real, MEI) → **certificado digital A1** (upload cifrado, ativação) → **CSC** (id + token) → **série/numeração** (`provider_auto` ou `marketfy_controlled`) → **emissão de teste em homologação**. Depois o mercado muda de `homologacao` para `producao`. Também registra o webhook do Neectify.

### Regras tributárias por produto
Perfis e regras (`product_tax_profiles` / `product_tax_rules`: NCM, CFOP, CST/CSOSN, ICMS, PIS, COFINS, ST, FCP) seguem o ciclo `draft → homologated → published → retired`, com **aprovação contábil**, autorização SEFAZ registrada e histórico de atribuição ao produto. Na venda, a regra vira um **snapshot imutável com hash**, validado antes de transmitir. O rollout é controlado por mercado (`off | warn | block`) e por flag global (`FISCAL_PRODUCT_RULES_ENABLED`); grupos de ICMS só passam se constarem em `FISCAL_APPROVED_ICMS_GROUPS`.

### Outras operações
Cancelamento de NFC-e, **inutilização** de numeração, reprocessamento de documentos com falha, impressão do DANFE (`nfce_print_service`), notificações fiscais ao lojista, **cota mensal por plano + créditos avulsos** (pacotes comprados, com reset mensal por cron) e painel admin para conceder créditos e controlar o rollout.

---

## 4. Modelo de dados (resumo)

- **Identidade/Planos:** `plans`, `users`, `refresh_sessions`, `markets`, `market_locations`, `market_members`.
- **Operação:** `products`, `stock_movements`, `terminals`, `boxes`, `box_movements`, `sales`, `sale_items`, `payments`, `customers`, `customer_ledger`, `financial_transactions`, `tickets`/`ticket_messages`.
- **Billing da plataforma:** `billing_subscriptions`, `billing_events`, `billing_invoices`, `invoices`.
- **Fiscal:** `fiscal_tenant_configs`, `product_tax_profiles`, `product_tax_rules` (+ approvals, SEFAZ authorizations, assignments), `fiscal_documents`, `fiscal_attempts`, `fiscal_artifacts`, `fiscal_events`, `fiscal_usage_counters/ledger`, `fiscal_emission_packages`, `fiscal_notifications`, `fiscal_inutilizacoes`, `provider_webhook_events`.
- **Pix:** `mercadopago_connections`, `mercadopago_oauth_states`, `pix_payment_attempts`, `pix_status_queries`, POS/Store registrations.
- **Funis de venda:** `funnels`, `funnel_variants`, `funnel_steps`, `funnel_sessions`, `funnel_events`.
- **Auditoria:** `audit_logs`.

---

## 5. Segurança e operação

- JWT de acesso curto (15 min) + refresh token em cookie HttpOnly (14 dias), com sessões persistidas.
- Autorização por papel e por vínculo com o mercado; isolamento multi-tenant (há testes específicos de tenant isolation no Pix).
- Rate limiting (memória ou Redis), segredos cifrados (`secret_cipher`), sanitização de logs, auditoria de ações, endpoint de métricas protegido por token, health checks.
- Webhooks com verificação de assinatura/segredo; locks Redis contra concorrência.
- Feature flags: `MP_ENABLED`, `PIX_LOCATION_ENABLED`, `BILLING_CORE_ENABLED`, `FISCAL_PRODUCT_RULES_ENABLED`, `FISCAL_APPROVED_ICMS_GROUPS`.
- Deploy via Docker (`Dockerfile.api`, `Dockerfile.worker`); configuração por `.env` (ver `backend/.env.example` e `frontend/.env.example`).

---

## 6. Como rodar (dev)

```bash
# Backend
cd backend
cp .env.example .env
pip install -r requirements.txt
alembic upgrade head
python run.py                      # API em :8000
python -m arq worker.WorkerSettings  # worker (precisa de Redis)

# Frontend
cd frontend
cp .env.example .env
npm install && npm run dev         # :3000
npm test                           # vitest
```

Requisitos: PostgreSQL, Redis (para fila, locks, SSE e rate limit distribuído).

---

## 7. Pontos de atenção observados

- Filas fiscais (`retry`, `reconcile`, `notifications`, `maintenance`) estão **remapeadas para `fiscal:high`** (worker único); jobs de certificado e notificação de cota estão comentados no cron.
- Há `focus_nfe_provider.py` duplicado (em `providers/` e `providers/fiscal/`) — possível resquício de refatoração.
- `requirements.txt` lista `psycopg2` sem versão e `runtime.txt`/Dockerfile divergem de versões do Python (3.11 no Dockerfile; `.pyc` de 3.12/3.13 no repositório) — vale padronizar.
- Diretórios `__pycache__` e `.pytest_cache` presentes na árvore; conferir `.gitignore`.
- Notas internas indicam migrations recentes (ex.: `20260914_0021`) validadas só em SQLite — rodar upgrade/downgrade em Postgres de staging antes do deploy.
- O guard de assinatura legado (`users.plan_id` sem assinatura) é um potencial bypass de paywall (ver `backend/docs/agent_memory/plans-and-paywall-findings.md`).
- Rollout fiscal de regras tributárias deve permanecer desligado (`FISCAL_PRODUCT_RULES_ENABLED=false`) até um piloto aprovado por contador.
