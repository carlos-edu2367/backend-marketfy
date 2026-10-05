# Spec — Funis A/B de conversão (diagnóstico vs. dia do mercado)

- **Data:** 2026-09-15
- **Status:** Aguardando revisão do usuário antes do plano de implementação
- **Repos afetados:** `marketfy/frontend` (páginas dos funis, tracking, painel admin) e `marketfy/backend` (persistência de eventos/leads, endpoints públicos e de admin)
- **Origem:** dois protótipos HTML fornecidos pelo usuário (`marketfy-funil-a-diagnostico.html`, `marketfy-funil-b-dia-do-mercado.html`), decisões fechadas em conversa de brainstorming em 2026-09-15.

---

## 1. Contexto e objetivo

O usuário tem dois protótipos estáticos de funil de conversão para o Marketfy (PDV para mercados de bairro):

- **Funil A — "Diagnóstico"**: 11 perguntas objetivas sequenciais → captura de lead → tela de "análise" → resultado com score de controle (0-100) → oferta do plano Pro → downsell (plano Básico).
- **Funil B — "Dia do mercado"**: 5 cenários narrativos (internet caindo, estoque divergente, fiado, fiscal, fechamento do caixa) com escolha de resposta → tela de análise → resultado + oferta na mesma tela → downsell.

Objetivo: colocar os dois no ar como páginas reais do Marketfy, com o conteúdo e a lógica dos protótipos preservados, mas no estilo visual real do produto — e principalmente, conseguir medir **qual funil converte mais** e **em qual etapa as pessoas mais abandonam**, por variante.

## 2. Decisões já fechadas

1. **Rotas dedicadas**: `/funil-a` e `/funil-b`. Sem split automático de tráfego numa URL só — o usuário decide manualmente para onde manda tráfego de campanha.
2. **Páginas autocontidas**: cada funil é um componente próprio (`FunilDiagnostico.jsx`, `FunilDiaDoMercado.jsx`), não um "motor de funil" genérico — as duas estruturas (11 perguntas objetivas vs. 5 cenários com pontuação) são diferentes o bastante para que uma abstração comum custe mais do que economiza, com só 2 funis para manter.
3. **Estilo**: paleta e componentes reais do Marketfy (`brand.yellow #FACC15`, `brand.dark #1F2937`, `brand.green #16A34A`, fundo branco/`brand.gray`, fonte Inter, `Button`/`Input`/`lucide-react` existentes) — não os tons dos protótipos (verde-oliva/creme no B, azul-marinho no A).
4. **Preço/oferta real**: plano exibido vem de `/identity/plans` (mesma fonte que `Home.jsx`/`Pricing.jsx` já usam via `usePublicPlans`), nunca hardcoded. A âncora "de R$ 997,00/mês" é mantida como copy fixo — é uma referência real: o valor que um mercado de médio/grande porte do interior de Goiás pagava pelo plano Pro antes de o Marketfy padronizar o preço de R$259,90/mês para mercados pequeno/médio porte.
5. **Lead capturado é real**: nome + whatsapp (+ email/cidade quando fornecidos) são persistidos no backend, não só no navegador como nos protótipos.
6. **Tracking duplo**: cada troca de tela dispara um evento no PostHog (via `lib/analytics.js`, já existente) **e** um evento leve no backend próprio — o backend é a fonte de dados do painel admin (sem depender de API externa, sem chave de PostHog no servidor). O PostHog continua recebendo os mesmos eventos para quem quiser explorar por lá também.
7. **Painel admin novo**: `/admin/funis`, mostrando funil de conversão por etapa (com taxa de queda) e lista de leads, comparando os dois funis lado a lado.
8. **CTA final** leva para `/register?plan={id}&cycle=monthly`, igual ao padrão já usado por `Pricing.jsx` — nunca `alert()`.

## 3. Design — Frontend

### 3.1 Estrutura de arquivos

```
src/pages/marketing/
  FunilDiagnostico.jsx       (Funil A)
  FunilDiaDoMercado.jsx      (Funil B)
src/components/marketing/
  FunnelProgressBar.jsx      (barra de progresso por etapa)
  FunnelOfferCard.jsx        (card de oferta — recebe o plano real via prop)
  FunnelScoreRing.jsx        (anel de score, usado no resultado)
src/lib/
  funnelTracking.js          (visitor id + track duplo PostHog/backend)
```

### 3.2 Rotas (`App.jsx`)

Duas rotas públicas novas, fora de `AdminLayout`/`SaaSLayout`, sem o header/nav da Home:

```jsx
<Route path="/funil-a" element={<FunilDiagnostico />} />
<Route path="/funil-b" element={<FunilDiaDoMercado />} />
```

### 3.3 Visitor id e tracking duplo

`src/lib/funnelTracking.js` expõe:

- `getFunnelVisitorId()`: lê/gera um id em `localStorage` (`marketfy_funnel_visitor_id`, `crypto.randomUUID()`). Reaproveita o `distinct_id` do PostHog quando o PostHog já estiver inicializado (consentimento de cookies aceito), para permitir cruzar os dois sistemas; cai para o id local quando não.
- `trackFunnelEvent(eventName, { funnel_variant, step, ...props })`: chama `track()` do PostHog (`lib/analytics.js`, já existente — não dispara nada se o usuário não aceitou cookies) **e** faz `POST /api/v1/marketing-funnel/events` em paralelo (fire-and-forget, sem bloquear a navegação — falha de rede no tracking não pode travar o funil).

Eventos disparados (mesmo vocabulário nos dois funis, propriedade `funnel_variant: 'A' | 'B'` diferenciando):

| Evento | Quando |
|---|---|
| `marketfy_funnel_start` | usuário clica em "começar" na intro |
| `marketfy_funnel_step_view` | toda troca de tela (`step` = id da etapa, ex. `q1`, `s3`, `analysis`) |
| `marketfy_lead_submit` | formulário de nome/contato enviado com sucesso |
| `marketfy_offer_view` | tela de oferta principal exibida |
| `marketfy_offer_cta_click` | clique no CTA do plano Pro |
| `marketfy_offer_decline` | clique em "quero algo mais enxuto" (abre downsell) |
| `marketfy_downsell_cta_click` | clique no CTA do plano Básico |

Isso é uma continuação direta do que os protótipos já faziam com `track()`/`dataLayer` — só passa a persistir também no backend.

### 3.4 Oferta e CTA

`FunnelOfferCard` recebe o plano recomendado (via `usePublicPlans`, igual `Pricing.jsx`) e monta o link `/register?plan={plan.id}&cycle=monthly`. Downsell usa o plano pago de menor `price_monthly` que não seja o recomendado; se só existir 1 plano pago ativo, a seção de downsell não é renderizada (sem quebrar o fluxo).

### 3.5 Lógica de pontuação

Preservada 1:1 dos protótipos: Funil A calcula `getControlScore()` a partir da soma de `pain` por resposta; Funil B calcula `quality()` por cenário e `score()` geral. Essas funções migram como estão (JS puro), só trocando os nomes de variáveis do português informal do protótipo por algo consistente com o resto do código, se necessário.

## 4. Design — Backend

### 4.1 Modelo de dados (novo, em `app/infra/database/models.py`)

Duas tabelas novas, sem relação de FK obrigatória com `users` (visitante é sempre anônimo até virar lead):

**`marketing_funnel_events`** — um evento por linha, vocabulário aberto (mesmo `event_name` que o frontend manda pro PostHog):

| coluna | tipo | notas |
|---|---|---|
| `id` | UUID PK | |
| `visitor_id` | String, indexado | id gerado no browser |
| `funnel_variant` | String(1) | `'A'` ou `'B'` |
| `event_name` | String | ex. `marketfy_funnel_step_view` |
| `step` | String, nullable | id da etapa quando aplicável |
| `properties` | JSONB, nullable | payload livre (ex. plano clicado) |
| `created_at` | DateTime, indexado | |

**`marketing_funnel_leads`**:

| coluna | tipo | notas |
|---|---|---|
| `id` | UUID PK | |
| `visitor_id` | String, indexado | liga o lead aos eventos anteriores dele |
| `funnel_variant` | String(1) | |
| `name` | String | |
| `phone` | String, nullable | |
| `email` | String, nullable | |
| `city` | String, nullable | |
| `control_score` | Integer, nullable | score calculado no momento da submissão |
| `answers` | JSONB | respostas brutas do funil |
| `created_at` | DateTime | |

Migração Alembic nova (`alembic revision --autogenerate`), seguindo o padrão das 47 migrações existentes.

### 4.2 Router público (`app/infra/web/routers/marketing_funnel.py`, prefixo `/api/v1/marketing-funnel`)

- `POST /events` — sem auth. Body: `{visitor_id, funnel_variant, event_name, step?, properties?}`. Rate limit via `enforce_rate_limit_async` (bucket `marketing_funnel_event`, ex. 60 req/min por IP — é tracking de navegação, alto volume esperado).
- `POST /leads` — sem auth. Body: `{visitor_id, funnel_variant, name, phone?, email?, city?, control_score?, answers}`. Valida `name` obrigatório e pelo menos um de `phone`/`email` (mesma regra do protótipo). Rate limit mais restrito (bucket `marketing_funnel_lead`, ex. 5 req/min por IP — evita spam de leads falsos).

Ambos retornam `202`/`201` mínimo (sem corpo relevante) — o frontend não depende da resposta além do status.

### 4.3 Router admin (mesmo arquivo ou `marketing_funnel_admin.py`, prefixo `/api/v1/admin`, protegido por `require_admin` — padrão de `admin.py`)

- `GET /marketing-funnel/summary?variant=A|B` — para cada `event_name` de etapa (ordem de etapas fixa em uma constante no backend, uma lista por funil, ex. `FUNNEL_A_STEPS = ['q1', 'q2', ..., 'lead_submit', 'offer_view', 'offer_cta_click']`), retorna contagem de `visitor_id` distintos que alcançaram aquela etapa e a taxa de queda em relação à etapa anterior.
- `GET /marketing-funnel/leads?variant=A|B` — lista paginada dos leads capturados, mais recentes primeiro.

### 4.4 Privacidade

Mesmo racional já registrado no spec de analytics (D9): visitante anônimo, sem PII até o lead ser enviado. Nenhum IP bruto é armazenado nas tabelas — `enforce_rate_limit_async`/`get_client_ip` usam o IP só em memória/Redis para o rate limit, não persistem no banco.

## 5. Design — Painel admin (frontend)

Nova página `src/pages/admin/MarketingFunnels.jsx`, item novo no menu do `SaaSLayout.jsx` (ex. ícone `TrendingUp`, label "Funis A/B"). Layout:

- Dois cards lado a lado (Funil A / Funil B), cada um com: total de visitantes que iniciaram, funil de etapas com barra de queda (usando `recharts`, já instalado — mesmo padrão de gráfico usado no resto do admin), taxa lead → clique no CTA.
- Tabela de leads capturados (nome, whatsapp, funil de origem, score, data), com filtro por funil.

## 6. Testes

- Frontend: teste de unidade para as funções de pontuação (`getControlScore`, `quality`/`score`) migradas dos protótipos; teste de que `funnelTracking.trackFunnelEvent` chama tanto `track()` quanto o `POST` do backend.
- Backend: testes de integração para `POST /events` e `POST /leads` (happy path + rate limit 429 + validação de lead sem nome/contato), e para `GET /marketing-funnel/summary` (conta visitantes distintos e calcula queda corretamente com dados de fixture).

## 7. Fora de escopo

- Split automático de tráfego / redirecionamento aleatório entre A e B numa mesma URL.
- Qualquer fluxo de follow-up comercial automatizado sobre os leads (CRM, disparo de WhatsApp, etc.) — o painel só lista os leads capturados.
- Mudar a Home (`/`) atual ou a `Pricing.jsx` — ficam como estão.
