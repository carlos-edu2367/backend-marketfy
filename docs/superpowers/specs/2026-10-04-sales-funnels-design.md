# Spec — Funis de venda com HTML por etapa e A/B

- **Data:** 2026-10-04
- **Status:** Aguardando revisão do usuário antes do plano de implementação
- **Repos afetados:** `marketfy/backend` (modelo, API pública e admin, atribuição, métricas), `marketfy/frontend` (página pública `/f/:slug`, editor e painel no admin, `Register.jsx`)
- **Referência de produto:** FunnelFox (funis web → paywall → assinatura), em versão enxuta.

---

## 1. Contexto e objetivo

Hoje o Marketfy só emite eventos de conversão para o PostHog (spec D9, `2026-09-14-product-analytics-posthog-design.md`) e não tem nenhum conceito de funil. O objetivo é permitir que o **admin da plataforma** crie funis de venda dos **planos do Marketfy**, divulgue-os (anúncios, campanhas) e acompanhe a conversão deles **dentro do próprio sistema**, da visita até a primeira fatura paga.

### Decisões tomadas com o usuário
| Tema | Decisão |
|---|---|
| Quem cria / o que vende | Admin da plataforma, vendendo planos do Marketfy |
| Tipos de etapa | Só HTML livre por etapa; final sempre no cadastro e checkout existentes |
| Onde ficam as métricas | Tabelas próprias + painel no admin (sem depender do PostHog) |
| Origem e testes | Captura de UTM/referrer **e** A/B por variantes com peso |
| JavaScript nas etapas | Permitido, isolado em `<iframe sandbox>` sem `allow-same-origin` |
| Arquitetura | Rota pública no próprio SPA React (`/f/:slug`) + endpoints no FastAPI |

### Fora do escopo (v1)
Versionamento de funil · etapas prontas (pergunta/oferta) · editor visual ou com destaque de sintaxe · domínio próprio · encerramento automático de A/B · envio dos eventos de funil ao PostHog · pré-agregação de métricas · funis para lojistas.

---

## 2. Modelo de dados

Uma migration Alembic nova (`YYYYMMDD_00NN_sales_funnels`), com quatro tabelas. `users` não muda.

### `funnels`
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `slug` | String, único, indexado | `^[a-z0-9]+(-[a-z0-9]+)*$`, até 80 chars |
| `name` | String | |
| `status` | String | `draft` \| `published` \| `archived` (CheckConstraint) |
| `plan_id` | UUID FK `plans.id`, nullable | plano pré-selecionado no cadastro |
| `tracking_html` | Text, nullable | pixels/scripts; até 50 KB |
| `created_by` | UUID FK `users.id` | |
| `created_at`, `updated_at` | DateTime | |

### `funnel_variants`
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `funnel_id` | UUID FK, indexado | |
| `name` | String | "A", "Controle"… |
| `weight` | Integer ≥ 0 | sorteio proporcional à soma das ativas |
| `is_active` | Boolean | |
| `position` | Integer | a de menor `position` é o **controle** na comparação |
| `created_at` | DateTime | |

Todo funil é criado com uma variante "A", peso 100.

### `funnel_steps`
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `variant_id` | UUID FK, indexado | |
| `position` | Integer | único por variante (`UniqueConstraint(variant_id, position)`) |
| `name` | String | |
| `html` | Text | até 200 KB |
| `created_at`, `updated_at` | DateTime | |

### `funnel_sessions`
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | é o `fsid` |
| `funnel_id`, `variant_id` | UUID FK, indexados | |
| `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term` | String(200), nullable | capturados só na criação |
| `referrer` | String(500), nullable | |
| `user_id` | UUID FK `users.id`, nullable, indexado | |
| `last_step_position` | Integer, nullable | |
| `completed_at`, `registered_at`, `trial_at`, `subscribed_at`, `paid_at` | DateTime (timestamptz), nullable | marcos |
| `first_payment_amount` | Numeric(10,2), nullable | gravado junto com `paid_at` |
| `created_at` | DateTime (timestamptz), indexado | define a coorte |

Índice composto `(funnel_id, created_at)`. Não guarda IP nem user agent completo (LGPD).

### `funnel_events`
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `session_id`, `funnel_id`, `variant_id` | UUID FK | |
| `type` | String | `step_view`, `step_next`, `funnel_completed`, `registered`, `trial_activated`, `subscription_created`, `invoice_paid` |
| `step_position` | Integer, nullable | só para eventos de etapa |
| `occurred_at` | DateTime (timestamptz) | |

Índices `(funnel_id, occurred_at)` e índice único parcial `(session_id, step_position) WHERE type = 'step_view'` para dedupe.

### Comportamento
- **A edição de um funil publicado vale imediatamente.** Os eventos guardam `step_position`; o editor exibe avisos (seção 4).
- Modelos usam `postgresql.UUID`: os testes em SQLite dependem do shim `@compiles(UUID, "sqlite")` já existente em `tests/conftest.py`.

---

## 3. Fluxo do visitante (público)

### 3.1 Endpoints públicos (`/api/v1/funnels/public`, sem auth)
- `POST /{slug}/session` com corpo `{fsid?, utm_*?, referrer?}`:
  - funil inexistente, não `published` ou sem variante ativa com peso > 0 → **404**;
  - `fsid` informado, existente e do mesmo funil → retoma a sessão (mesma variante);
  - caso contrário → sorteia a variante pelo peso, cria a sessão;
  - resposta: `{fsid, funnel: {name, plan_id}, variant: {id}, tracking_html, steps: [{position, name, html}], resume_position}`.
- `POST /events` com corpo `{fsid, type, step_position?}`:
  - `type` aceito apenas em `step_view | step_next | funnel_completed` (outros → 422);
  - `step_view` deduplicado por (sessão, posição); atualiza `last_step_position`; `funnel_completed` preenche `completed_at` se nulo;
  - sessão inexistente → 404; funil despublicado → 410 (o front ignora);
  - **rate limit** com o `rate_limiter` existente, por IP e por `fsid`.

O sorteio usa `random.choices` com os pesos das variantes ativas; isolado numa função pura (`pick_variant`) para teste.

### 3.2 Página `/f/:slug` (React)
1. Lê `localStorage['funnel:<slug>']` (fsid) e os UTMs/`document.referrer` da URL.
2. Chama `POST /{slug}/session`; grava o `fsid` retornado.
3. Renderiza a etapa `resume_position` (ou a primeira) num iframe de tela cheia (`100dvh`):
   `<iframe sandbox="allow-scripts allow-forms allow-popups" srcdoc={...}>` — **sem** `allow-same-origin` e **sem** `allow-top-navigation`.
4. `srcdoc` = `<!doctype html>` + `tracking_html` + helper `Funnel` + HTML da etapa.
5. Envia `step_view` ao exibir cada etapa; `step_next` ao avançar.
6. Na última etapa, `next` ou `finish` → envia `funnel_completed` e redireciona:
   - deslogado: `/register?plan=<plan_id>&fsid=<fsid>`;
   - logado: `/plans?plan=<plan_id>&fsid=<fsid>`.
7. Grava também `sessionStorage['funnel_fsid']` para sobreviver à navegação até o checkout.

A rota fica num chunk carregado sob demanda (`React.lazy`) para não pesar o bundle do app.

### 3.3 Helper injetado e protocolo `postMessage`
O helper expõe `window.Funnel.next()`, `.back()` e `.finish()` e liga automaticamente cliques em elementos com `data-funnel-next`, `data-funnel-back` e `data-funnel-finish`. Cada chamada faz:
```js
parent.postMessage({ source: 'marketfy-funnel', type: 'next' }, '*');
```
O pai só processa mensagens em que `event.source === iframeRef.current.contentWindow`, `data.source === 'marketfy-funnel'` e `data.type ∈ {next, back, finish}`. Todo o resto é ignorado.

### 3.4 Prévia do admin
`/f/<slug>?preview=<variant_id>`: só para admin logado, via `GET /api/v1/admin/funnels/{id}/variants/{vid}/preview`. Funciona em `draft`, **não cria sessão nem grava eventos**, e mostra uma barra fixa fora do iframe com navegação livre entre etapas.

### 3.5 Falhas no front
Envio de evento falho → uma nova tentativa, depois descarte silencioso. O funil nunca bloqueia por causa de métrica. 404/410 → página `NotFound`.

---

## 4. Editor no admin

### 4.1 Telas
- **`/admin/funnels`:** tabela com nome, slug, status, nº de variantes, sessões e conversão em pagamento nos últimos 30 dias (uma consulta agregada). Ações: criar, duplicar, arquivar. Item novo **Funis** no `AdminLayout`.
- **`/admin/funnels/:id`**, em três abas:
  1. **Configuração:** nome, slug, plano de destino (planos ativos), `tracking_html` (textarea), publicar, despublicar e arquivar.
  2. **Etapas:** seletor de variante; criar ou duplicar variante; pesos e ativa/inativa. Lista de etapas (adicionar, renomear, reordenar ↑↓, excluir). Textarea monoespaçado com o HTML e prévia ao vivo no mesmo iframe sandbox + helper. Botão "Abrir prévia completa".
  3. **Métricas:** seção 5.

### 4.2 API admin (`/api/v1/admin/funnels`, papel `admin`)
- `GET /`, `POST /`, `GET /{id}`, `PATCH /{id}`
- `POST /{id}/duplicate` (copia variantes e etapas; novo slug `<slug>-copia[-n]`; status `draft`)
- `POST /{id}/publish`, `POST /{id}/unpublish` (volta a `draft`), `POST /{id}/archive`
- `POST /{id}/variants`, `PATCH /{id}/variants/{vid}`, `DELETE /{id}/variants/{vid}` (bloqueado se for a única variante), `POST /{id}/variants/{vid}/duplicate`
- `POST /{id}/variants/{vid}/steps`, `PATCH .../steps/{sid}`, `DELETE .../steps/{sid}`, `PUT /{id}/variants/{vid}/steps/order` (lista ordenada de ids)
- `GET /{id}/variants/{vid}/preview`
- `GET /{id}/metrics` (seção 5)

### 4.3 Validações
- **Publicar exige:** ≥ 1 variante ativa com peso > 0, ≥ 1 etapa em cada variante ativa e `plan_id` apontando para um plano ativo.
- Slug único e no formato; HTML de etapa ≤ 200 KB; `tracking_html` ≤ 50 KB; peso inteiro ≥ 0.
- **Avisos que não bloqueiam** (o endpoint devolve `warnings`):
  - alterar, reordenar ou excluir etapas de funil `published` com sessões → "isso afeta as métricas por etapa";
  - alterar pesos ou ativação de variantes com sessões → "comparações anteriores ficam enviesadas".

### 4.4 Auditoria
Criar, publicar, despublicar, arquivar, duplicar e alterar pesos geram `record_audit_event` (padrão de `infra/observability/audit.py`).

---

## 5. Métricas

### 5.1 Base de cálculo
**Coorte por data de entrada:** o filtro `from`/`to` (padrão: últimos 30 dias) seleciona as sessões por `created_at`. Cada sessão conta para todos os marcos que atingir, mesmo depois do período. O painel avisa que coortes recentes ainda estão amadurecendo.

**Excluídos:** sessões cujo `user_id` é de um usuário com papel `admin`. A prévia nunca cria sessões.

### 5.2 Endpoint
`GET /admin/funnels/{id}/metrics?from&to&variant_id&utm_source&utm_campaign` responde:
- `summary`: sessões, concluíram, cadastros, trials, assinaturas, pagamentos, `paid_conversion` (pagamentos ÷ sessões) e `revenue` (soma de `first_payment_amount`);
- `steps`: por posição, sessões com `step_view` na posição, % sobre o total e queda em relação à anterior; seguido de concluiu → cadastro → trial → assinatura → pago;
- `variants`: por variante, peso, sessões, cadastros, pagamentos, conversão, receita por sessão e `confidence` em relação ao controle;
- `sources`: agrupado por `utm_source` / `utm_campaign`, com sessões, cadastros, pagamentos e conversão; nulos aparecem como "direto / sem origem";
- `timeseries`: sessões e pagamentos por dia.

### 5.3 Confiança do A/B
Teste z de duas proporções sobre a conversão em pagamento, em função pura (`two_proportion_confidence`). Com menos de 100 sessões em qualquer das variantes, ou menos de 10 conversões somadas, devolve `confidence: null, low_sample: true`. O painel mostra "amostra pequena".

### 5.4 Implementação
SQL agregado sob demanda (`FunnelMetricsRepository`) sobre `funnel_sessions` (marcos) e `funnel_events` (etapas). Sem jobs nem tabelas de pré-agregação. Gráficos com `recharts`, já presente no frontend.

---

## 6. Integração com o fluxo existente

### 6.1 Atribuição
O serviço novo `FunnelAttributionService` (`application/services/funnel_attribution_service.py`) tem duas operações:
- `link_registration(fsid, user_id)`: valida que a sessão existe e está sem usuário; preenche `user_id` e `registered_at` e grava o evento `registered`.
- `mark(user_id, milestone, amount=None)`: busca a sessão **mais recente** daquele usuário; sem sessão, não faz nada. Preenche o marco **só se estiver nulo** (idempotente) e grava o evento.

Ambas rodam **depois** do commit de sucesso e nunca propagam exceção (try/except + log, padrão do `PostHogClient`).

| Marco | Ponto de chamada (o mesmo do evento PostHog) |
|---|---|
| `registered` | `POST /api/v1/identity/register` — DTO ganha `funnel_session_id: Optional[UUID]` |
| `trial_activated` | `subscription_service.py` (onde está `track_event('trial_activated')`) |
| `subscription_created` | `invoice_service.py` e `recurring_service.py` |
| `invoice_paid` | `invoice_service.py`, só no primeiro pagamento (marco nulo), com o valor da fatura |

### 6.2 Usuário já logado
O endpoint autenticado `POST /api/v1/funnels/sessions/{fsid}/claim` é chamado por `Plans.jsx` quando há `fsid` na URL ou no `sessionStorage`. Associa a sessão ao usuário logado se ela estiver sem dono; os marcos posteriores passam a contar.

### 6.3 Frontend existente
- `Register.jsx`: lê `fsid` (URL → `sessionStorage`) e envia `funnel_session_id` no registro.
- `Plans.jsx`: faz o *claim* descrito acima; o `plan` da URL já é tratado pelo `planIntent` existente.

### 6.4 Casos de borda
- `fsid` inválido, de outro funil ou já vinculado a outro usuário → cadastro normal, sem atribuição, com log.
- Usuário que passou por dois funis antes de cadastrar → vale o `fsid` do cadastro (último toque).
- Funil despublicado no meio da sessão → 404 para o visitante; dados preservados nas métricas.

---

## 7. Segurança
- HTML e JS das etapas rodam em iframe de **origem opaca** (sem `allow-same-origin`): não acessam cookies, `localStorage` nem tokens do app, e não navegam a aba principal.
- O pai valida a origem (`event.source`) e o formato de toda mensagem.
- Endpoints públicos com rate limit e só aceitam eventos de navegação; marcos de negócio só são gravados pelo backend.
- API admin restrita ao papel `admin` e auditada.
- Sem IP nem user agent completo nas sessões.

---

## 8. Testes
- **Unit (backend):** `pick_variant` (distribuição, inativas, peso 0); validação de publicação; dedupe de `step_view`; idempotência de `mark`; `link_registration` com fsid inválido, de outro funil ou já vinculado; `two_proportion_confidence` e `low_sample`; métricas por coorte (sessão antiga convertendo depois do período).
- **Integration (backend):** fluxo completo sessão → eventos → registro com `fsid` → trial → assinatura → fatura paga → métricas; rate limit do endpoint público; 403 na API admin para não-admin; prévia sem gravar nada; exclusão de sessões de admin; migration com `upgrade` → `downgrade` → `upgrade` **em Postgres** (não só SQLite).
- **Frontend (Vitest):** `/f/:slug` com retomada pelo `localStorage`, envio de eventos e redirect final com `fsid` (logado e deslogado); filtro do `postMessage`; `Register.jsx` enviando `funnel_session_id`; `Plans.jsx` fazendo *claim*; editor com prévia ao vivo e avisos.

---

## 9. Riscos
- **Edição ao vivo distorce métricas por etapa.** Mitigado pelos avisos; versionamento é a evolução natural se virar problema.
- **Carregamento do SPA em tráfego pago.** Mitigado com o chunk sob demanda; se não bastar, servir `/f/*` pelo FastAPI (abordagem 2 avaliada) sem mudar o modelo.
- **JS de terceiros (pixels) dentro do iframe.** Isolado por design; o risco residual é só sobre o próprio conteúdo do funil.
- **Consultas sob demanda** podem ficar lentas com volume alto; os índices cobrem o caso atual, e a pré-agregação fica como próximo passo.
