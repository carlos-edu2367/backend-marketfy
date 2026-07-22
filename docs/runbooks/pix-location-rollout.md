# Pix presencial: localização e sincronização de lojas

## Objetivo

Habilitar o QR dinâmico por mercado somente quando o endereço estruturado e o
ponto confirmado da loja estiverem disponíveis para o Mercado Pago.

## Pré-requisitos de produção

- Aplicar a migration `20260721_0019_market_locations_and_mp_store_sync`.
- Configurar `PIX_LOCATION_ENABLED=true` depois da migration e da validação em
  staging. O kill switch `false` impede novas operações Pix pelo PDV.
- Configurar `VITE_MAP_TILES_URL` com um provedor de mapas contratado e revisar
  a atribuição exigida pelo provedor. O fallback OpenStreetMap é apenas para
  desenvolvimento.
- Manter `RATE_LIMIT_BACKEND=redis` em produção para que a consulta de CEP seja
  limitada de forma consistente entre réplicas.

## Rollout por mercado

1. Selecionar um mercado piloto e confirmar OAuth, endereço, coordenadas e
   registro de Store/POS no Mercado Pago.
2. Gerar um QR de baixo valor em sandbox/homologação e observar criação,
   eventos SSE, fallback de polling, webhook e conclusão da venda.
3. Expandir em lotes, acompanhando `marketfy_pix_location_events_total`,
   `marketfy_pix_qr_created_total`, erros HTTP de criação e divergências.
4. Manter o Pix desabilitado no PDV para mercados sem localização pronta; a
   configuração é individual por mercado e não deve ser copiada entre tenants.

## Diagnóstico

- `pix.location_not_configured`: abrir Configurações → Pix no mercado correto,
  preencher o endereço, confirmar o marcador e salvar.
- `pix.location_invalid`: revisar CEP, UF, país, campos obrigatórios e se o
  marcador está dentro de latitude/longitude válidas.
- `sync_required`: a localização mudou; repetir um teste de QR para atualizar
  o Store antes de criar a cobrança.
- QR pendente sem atualização: verificar conexão SSE, fallback de polling,
  webhook do Mercado Pago e o `request_id` registrado nos logs. Não duplicar a
  cobrança manualmente; a API aceita chave idempotente por tentativa.

## Rollback

- Para interromper o rollout, definir `PIX_LOCATION_ENABLED=false` e preservar
  os dados de localização para diagnóstico.
- Para falha de um único mercado, desabilitar `enabled_in_pdv` desse mercado e
  corrigir o endereço/sincronização antes de reativar.
- Não remover as tabelas em produção como rollback operacional. O downgrade da
  migration só deve ser usado em ambiente controlado, após verificar que não há
  `market_locations` ou registros de Store necessários.

## Segurança e privacidade

Logs e métricas não devem conter endereço completo, coordenadas, token OAuth,
QR copia-e-cola ou payload do Mercado Pago. Auditoria registra ator, mercado,
versão da localização e resultado; o acesso ao endereço segue as permissões
`payments.read`/`payments.write`.
