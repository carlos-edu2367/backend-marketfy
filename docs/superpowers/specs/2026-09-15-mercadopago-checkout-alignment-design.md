# Marketfy ↔ Billing Core: alinhamento do checkout com o Mercado Pago — Design Spec

**Contexto:** o billing-core tornou o Mercado Pago o gateway padrao (`billing-core/docs/superpowers/plans/2026-09-14-mercadopago-gateway-padrao.md`, ja em producao). O Marketfy consome `POST /v1/payments` (checkout avulso: creditos fiscais e faturas de assinatura) e `POST /v1/subscriptions` (assinatura recorrente por cartao). Esta spec fecha as lacunas encontradas na analise de 2026-09-15 (ver [[billing-mp-alignment-analysis]]) para que **toda contratacao e todo pagamento no Marketfy passem por checkout**, de forma amigavel, funcional e limpa — a pedido explicito do usuario.

Depende de `billing-core/docs/superpowers/specs/2026-09-15-subscription-checkout-support-design.md` (campo `back_url` e `GET /v1/subscriptions/{id}`), que precisa estar publicado no billing-core antes da Fase 2 abaixo.

## Estado atual (resumo da analise)

- **Creditos fiscais e faturas de assinatura** (`billing_mode=invoice`) ja usam `POST /v1/payments` corretamente e ja tem pagina de retorno para creditos (`/dashboard/fiscal/credits/return`). Fica quase tudo igual.
- **Assinatura por cartao** (`billing_mode=recurring`) esta quebrada: `RecurringService.contract` consulta o job do billing-core uma unica vez, logo apos criar (linha `checkout_url, billing_sub_id = await self._poll_subscription_job(job_id)`), quando o job ainda nao terminou. `billing_subscription_id` e `checkout_url` ficam vazios, o usuario nunca ve o link de pagamento, e o webhook (que so acha a assinatura por `billing_subscription_id`) nunca ativa o plano.
- Sem pagina de retorno (`/billing/success|cancel|expired` nao existem no roteador do front — caem em 404).
- Sem cancelamento nem troca de plano segura.
- `system_sub_id = str(user.id)` e a chave de idempotencia por usuario+plano+ciclo fazem o billing-core devolver a assinatura antiga em vez de criar uma nova.
- Acesso calculado pela assinatura mais recente por `updated_at`, nao pela mais relevante — abandonar um checkout bloqueia quem ja tinha acesso.
- `expires_at` da assinatura recorrente vem de um valor fabricado (`agora + 5 anos`) e e ecoado de volta pelo billing-core sem controle de periodo real.

## Decisoes confirmadas (2026-09-15)

- **D1 — Cancelamento:** mantem acesso ate o fim do periodo ja pago. Nao ha estorno automatico.
- **D2 — Espera do Mercado Pago (~1h entre autorizar o cartao e a primeira fatura):** acesso provisorio de 24h a partir da autorizacao do cartao (detectada via `GET /v1/subscriptions/{id}` do billing-core, ver spec dependente), confirmado (ou revogado) quando o webhook real chegar.
- **D3 — Troca de plano:** sem calculo de valor proporcional. A nova assinatura so substitui a anterior quando o primeiro pagamento dela for confirmado; a anterior e cancelada nesse momento.
- **D4 — Clientes Asaas:** nao ha migracao a fazer. Todos os assinantes atuais do Marketfy usam o modo fatura (`billing_mode=invoice`, checkout avulso via `POST /v1/payments`), que nunca depende de `customer_provider_id`/gateway do assinante. O campo legado `asaas_customer_id` so seria tocado por um usuario que jamais contratou recorrencia; fica como esta, sem migracao de dados.
- **D5 — Estorno e chargeback:** bloqueiam o acesso imediatamente, sem carencia.

## Comportamento especificado

### 1. Assinatura por cartao — fluxo assincrono correto (espelha o que ja funciona para fatura)

Passo A — `POST /billing/subscribe` (`billing_mode=recurring`): cria a `BillingSubscriptionModel` local **antes** de chamar o billing-core, com `status="pending"`. Envia `system_sub_id = str(local_subscription.id)` (nao mais `user.id`) e uma chave de idempotencia no billing-core derivada do id local (`f"bc-sub-{local_subscription.id}"`, determinada, sem depender do usuario digitar nada duas vezes). Enfileira o job e devolve `{subscription_id: <id local>, job_id}` **sem** `checkout_url` (ele ainda nao existe).

Passo B — `POST /billing/subscriptions/{id}/checkout` (endpoint novo, mesma forma que `POST /billing/invoices/{id}/checkout` ja usa para fatura): consulta o job do billing-core (`GET /v1/jobs/{job_id}` via `BillingCoreClient.get_job`), e quando concluido grava `billing_subscription_id` e `checkout_url` na linha local e devolve `{status, checkout_url}`. Pode ser chamado repetidas vezes (o front faz polling, igual `fetchInvoiceCheckoutUrl` ja faz para fatura) sem duplicar nada no billing-core.

Isso por si so corrige a ativacao: uma vez que `billing_subscription_id` esta gravado, o webhook (`SubscriptionService.process_recurring_event`, que ja busca por `get_by_billing_subscription_id`) encontra a assinatura normalmente.

### 2. Idempotencia e recontratacao

O front gera uma chave de idempotencia nova a cada abertura do modal de contratacao (nao mais fixa por usuario+plano+ciclo+modo). Reenviar o mesmo `POST /billing/subscribe` por duplo clique reaproveita a mesma tentativa (`BillingSubscriptionModel.idempotency_key` unico, ja garantido pela coluna); abrir o modal de novo — mesmo para o mesmo plano — cria uma tentativa nova, o que permite recontratar depois de cancelar ou trocar de plano.

### 3. `expires_at` calculado localmente, nunca confiado ao payload do webhook

O Marketfy para de usar `subscription_expires_at` do payload do webhook do billing-core como fonte de verdade (hoje e literalmente o valor fabricado de 5 anos que o proprio Marketfy enviou, ecoado de volta). Ao processar `PAYMENT_RECEIVED`, calcula `expires_at = payment_date + PERIOD_DAYS[subscription_type]` (mesma tabela ja usada em `recurring_service.py`/`invoice_service.py`). Isso vale para os dois modos (fatura ja faz isso certo hoje via `period_end`; recorrente passa a fazer igual).

### 4. Cancelamento (D1)

Endpoint novo `POST /billing/subscription/cancel`. Nao apaga nem muda `status` na hora: marca `cancel_at_period_end=True` e `canceled_at=agora` na assinatura ativa do usuario. Se o modo for `recurring`, chama `POST /v1/subscriptions/{billing_subscription_id}/cancel` no billing-core (para parar a cobranca futura no cartao); se for `invoice`, so impede a proxima fatura (o job `generate_due_invoices` pula assinaturas com `cancel_at_period_end=True`). O acesso continua normalmente ate `expires_at`; depois disso, sem a carencia de 3 dias que existe para atraso de pagamento (usuario pediu para parar, nao atrasou).

### 5. Troca de plano (D3)

Contratar um plano novo enquanto ha uma assinatura ativa cria uma tentativa nova (idempotencia por abertura de modal, ver item 2) em paralelo. So quando o pagamento da nova assinatura confirma (`PAYMENT_RECEIVED`/`invoice_activated`) e que a assinatura anterior e marcada `cancel_at_period_end=True` com acesso imediato transferido para a nova (sem sobreposicao de cobranca: a antiga para de gerar fatura/cobranca a partir dai).

### 6. Acesso (`PlanAccessService`) prioriza status operacional, nao recencia

`get_active_by_owner` deixa de pegar so a linha mais recente por `updated_at`. Passa a preferir, nesta ordem: `active`/`trialing` com `expires_at` no futuro (ou dentro da carencia) > `cancellation_pending`-equivalente (`cancel_at_period_end=True` ainda dentro do periodo) > `pending` mais recente > qualquer outra. Isso evita que abrir e abandonar um checkout novo derrube o acesso de quem ja estava ativo.

`_effective_status` ganha uma regra nova: quando `cancel_at_period_end=True` e `agora > expires_at`, o resultado e `expired`/bloqueado **sem** a carencia de 3 dias (a carencia e so para quem atrasou, nao para quem pediu para parar).

### 7. Estorno e chargeback (D5)

`process_recurring_event` ganha dois eventos novos no `status_map`: `PAYMENT_REFUNDED` e `PAYMENT_CHARGEBACK_REQUESTED` passam a marcar `cancel_at_period_end=True` e `expires_at=agora` (bloqueio imediato, reaproveitando a mesma regra do item 6 sem precisar de um caminho de codigo separado).

### 8. Retorno do checkout — uma pagina so

Rota nova no front, `/billing/retorno`, que recebe `?tipo=credits|invoice|subscription&ref=<id local>` e consulta o endpoint certo ate confirmar (mesmo padrao de polling que `CreditPaymentReturn.jsx` ja faz). `success_url`/`cancel_url`/`expired_url` dos checkouts avulsos e o `back_url` da assinatura (usando o campo novo do billing-core) passam a apontar para ela. `/billing/success`, `/billing/cancel`, `/billing/expired` e `/dashboard/fiscal/credits/return` continuam existindo como redirecionamentos para `/billing/retorno` (checkouts ja abertos antes do deploy nao podem quebrar).

### 9. Acesso provisorio de 24h no cartao (D2)

Quando o usuario volta para `/billing/retorno?tipo=subscription`, o front chama `GET /billing/subscriptions/{id}/status` (proxy novo do Marketfy para o `GET /v1/subscriptions/{id}` do billing-core). Se o status ao vivo for `ACTIVE` mas a assinatura local ainda estiver `pending` (webhook da primeira fatura ainda nao chegou), o backend concede acesso provisorio: `status="active"`, `expires_at=agora+24h`, marcando `provisional=True` na linha. Quando o webhook real chega, `expires_at` e recalculado pelo item 3 (substitui o provisorio, `provisional=False`). Um job de reconciliacao (`reconcile_provisional_subscriptions`, no mesmo estilo de `reconcile_pending_invoices`) roda a cada execucao do scheduler existente e repete essa checagem para quem nao voltou pelo navegador (fechou a aba).

### 10. Textos e detalhes

- Modal: "Pix ou cartao · voce paga a cada periodo" / "Cartao de credito · renova automaticamente" (nada de "boleto", que o Marketfy exclui do checkout).
- Descricao enviada ao billing-core usa o nome do plano, nunca o UUID da fatura/assinatura.
- Primeira fatura do modo `invoice` conta o periodo a partir do pagamento confirmado, nao da criacao (mesma logica de `expires_at` local do item 3, aplicada tambem ao `period_start`/`period_end` no momento da ativacao).

## Mudancas de dados

Migration nova em `alembic/versions/`: adiciona a `billing_subscriptions`:
- `cancel_at_period_end BOOLEAN NOT NULL DEFAULT false`
- `canceled_at TIMESTAMP NULL`
- `provisional BOOLEAN NOT NULL DEFAULT false`

Nenhuma coluna existente muda de tipo ou é removida.

## Fora de escopo

- Qualquer migracao de dados de clientes Asaas (D4 — nao existe hoje).
- Valor proporcional na troca de plano (D3 — decisao explicita de nao ter).
- Estorno automatico de creditos fiscais ja consumidos.
- Mudanca de vocabulario de webhook do billing-core alem do que a spec dependente ja cobre.

## Global Constraints

- Nenhum contrato HTTP publico do Marketfy que o frontend ja usa muda de formato sem um caminho de compatibilidade (`/billing/success|cancel|expired` continuam existindo).
- `PUBLIC_FRONTEND_URL` continua sendo a unica fonte da origem do front; nenhuma URL nova hardcoded.
- Webhook do billing-core (`X-Webhook-Signature-256`) continua sendo a unica fonte de verdade para liberar acesso — a pagina de retorno e o acesso provisorio (item 9) nunca substituem essa validacao para o estado final.
- Commits convencionais, terminando com `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Suite completa verde ao fim de cada task (`pytest` no backend, `npm test` no frontend).
