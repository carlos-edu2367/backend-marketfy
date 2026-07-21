# Checkout sob demanda para faturas de assinatura

**Data:** 2026-07-21  
**Escopo:** Marketfy e Billing Core

## Contexto

No modo `invoice`, o Marketfy hoje cria a assinatura local, a fatura e o checkout no mesmo pedido. O worker que antecipa uma fatura também cria o checkout imediatamente. Como o checkout expira em 30 minutos, isso produz links vencidos antes de o usuário tentar pagar.

O botão `Ver Faturas` do cartão de plano também não possui ação e não abre a aba de faturas.

## Objetivo

Uma fatura deve existir antes do pagamento, mas o checkout só deve ser criado quando o usuário selecionar `Pagar`.

- A contratação inicial no modo fatura cria somente a assinatura pendente e a primeira fatura.
- O worker cria apenas faturas futuras, sem checkout.
- Abrir a fatura para pagamento cria ou reutiliza um checkout válido.
- Um checkout vencido pode ser reemitido para a mesma fatura; uma nova fatura não é criada.
- Eventos de uma tentativa antiga não podem alterar a tentativa atual de forma incorreta.

## Decisão

Será usado um endpoint explícito para solicitar o checkout da fatura. Endpoints `GET` permanecem somente de leitura.

### Marketfy

1. `InvoiceService.contract()` cria a assinatura e a primeira `billing_invoice`, mas não chama o Billing Core.
2. `InvoiceService.generate_next_invoice()` cria somente a `billing_invoice` futura.
3. Novo endpoint autenticado: `POST /api/v1/billing/invoices/{invoice_id}/checkout`.
   - Confirma que a fatura pertence ao usuário e está `pending`.
   - Se ainda não houver pagamento no Billing Core, chama `POST /v1/payments` para criar a primeira tentativa.
   - Se já houver `bc_payment_id`, solicita ao Billing Core a criação ou reutilização do checkout dessa cobrança.
   - Persiste o último `bc_job_id`, `bc_payment_id` e `checkout_url` retornados.
4. `GET /billing/invoices` e `GET /billing/invoices/{invoice_id}` não criam checkout.
5. O frontend navega para `/dashboard/settings?tab=invoices` após a contratação em modo fatura.
6. O botão `Ver Faturas` seleciona a aba `invoices`; a aba também honra `tab=invoices` na URL.
7. O botão `Pagar` chama o novo `POST`, redireciona quando houver `checkout_url` e informa que o checkout está sendo preparado quando o job ainda estiver em processamento.

### Billing Core

1. `POST /v1/payments` permanece o mecanismo de criação da primeira cobrança para uma fatura.
2. Novo endpoint interno: `POST /v1/payments/{payment_id}/checkout`.
   - Reutiliza um checkout pendente e válido.
   - Reemite checkout somente se a tentativa anterior estiver expirada ou cancelada.
   - Não cria outro `Payment` e não muda `system_payment_id`.
   - Retorna `202` com o job id quando o gateway precisa criar uma tentativa; chamadas idempotentes para a mesma tentativa retornam o mesmo job.
3. Será criada uma entidade/tabela de tentativas de checkout associada ao `Payment`. Cada tentativa registra o identificador do checkout do gateway, URL, expiração, status e timestamps.
4. O processamento de webhooks associa os eventos à tentativa correspondente. Apenas uma tentativa paga pode confirmar a fatura; uma expiração de tentativa antiga não pode cancelar uma tentativa reemitida.

## Fluxo

```text
Contratar plano (modo fatura)
  -> Marketfy grava assinatura pendente + fatura pendente
  -> Frontend abre Faturas

Pagar fatura
  -> Marketfy solicita checkout da fatura
  -> sem pagamento: Billing Core cria primeira tentativa
  -> tentativa válida: Billing Core devolve a URL existente
  -> tentativa expirada/cancelada: Billing Core cria nova tentativa da mesma cobrança
  -> Frontend abre a URL retornada

Webhook de pagamento confirmado
  -> Billing Core identifica a tentativa paga
  -> Marketfy ativa a fatura e a assinatura
```

## Concorrência e idempotência

- O Marketfy deve serializar a criação de checkout por `invoice_id` para cliques repetidos não enfileirarem pagamentos duplicados.
- A primeira tentativa usa uma chave de idempotência estável por fatura.
- O Billing Core controla a tentativa ativa e sua própria chave de idempotência; uma nova chave é criada somente após estado terminal elegível para reemissão.
- A fatura local conserva o mesmo `invoice_id` em todas as tentativas.

## Erros e interface

- Fatura não pertencente ao usuário: `404`.
- Fatura não pendente: `409`, sem novo checkout.
- Checkout em preparação: resposta com `job_id` e sem URL; a interface informa para tentar novamente em instantes.
- Indisponibilidade do Billing Core: `503`, mantendo a fatura pagável para uma nova tentativa posterior.

## Testes de aceitação

1. Contratação em modo fatura não chama a criação de checkout e devolve uma fatura pendente.
2. Worker de renovação cria fatura sem chamar o Billing Core.
3. Primeiro clique em `Pagar` cria uma tentativa e retorna/acompanha o job.
4. Novo clique com checkout válido reutiliza a mesma tentativa.
5. Novo clique após expiração reemite checkout para a mesma fatura e não cria uma nova `billing_invoice`.
6. Webhook da tentativa antiga não invalida a tentativa nova.
7. O botão `Ver Faturas` e `?tab=invoices` exibem a lista de faturas.
