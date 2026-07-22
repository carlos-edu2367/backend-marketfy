# Retry de assinatura por fatura cancelada

## Objetivo

Permitir que o usuário recupere uma contratação por fatura que foi cancelada antes do pagamento, criando uma nova assinatura e uma nova fatura pendente. O novo checkout continua lazy: só é criado quando o usuário clicar em **Pagar**.

## Escopo

- A tela de faturas mostra **Tentar novamente** para faturas com status `canceled`.
- A nova ação é autorizada pelo dono da fatura e opera somente sobre faturas canceladas que ainda não foram pagas.
- A assinatura e a fatura antigas permanecem como histórico cancelado; não haverá exclusão física.
- A ação cancela logicamente a assinatura antiga, cria uma nova assinatura `pending` com o mesmo plano e período escolhido e cria uma nova fatura `pending`.
- A nova fatura recebe período completo a partir do instante do retry e não recebe checkout nem chamada ao Billing Core nessa etapa.
- Após o retry, a interface recarrega a lista. O usuário precisa clicar em **Pagar** na nova fatura para iniciar o checkout lazy existente.

## Fluxo

1. Usuário abre **Faturas** e escolhe **Tentar novamente** em uma fatura cancelada.
2. `POST /billing/invoices/{invoice_id}/retry` valida posse, status e ausência de fatura pendente aberta para a assinatura de origem.
3. O serviço marca a assinatura antiga como `canceled` e cria uma nova assinatura `pending` e uma nova fatura `pending`.
4. A resposta contém o identificador da nova fatura, sem `checkout_url`.
5. O botão **Pagar** da nova fatura chama o endpoint de checkout sob demanda já existente.
6. O webhook de pagamento ativa somente a nova assinatura vinculada à nova fatura.

## Proteções

- Faturas `paid` e faturas não canceladas não podem gerar retry por esse endpoint.
- Uma fatura pendente aberta para a assinatura antiga bloqueia o retry, evitando duplicidade.
- A assinatura/fatura históricas não são modificadas para `pending` nem são apagadas.
- Falhas antes do commit não expõem uma nova assinatura parcialmente criada.

## Testes

- Serviço: retry cria assinatura e fatura novas sem checkout e preserva os registros antigos.
- Rota: valida dono e rejeita status que não seja `canceled`.
- Interface: mostra **Tentar novamente** apenas para canceladas e usa a nova ação.
- Regressão: a nova fatura só cria checkout após **Pagar**.
