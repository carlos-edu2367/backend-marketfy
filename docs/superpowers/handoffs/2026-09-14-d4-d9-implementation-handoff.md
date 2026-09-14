# Handoff — implementar D4 e D9

- **De:** sessão de 2026-09-14 (rodada 2 de conversão + D1/D4/D9)
- **Para:** próxima sessão
- **Pedido do usuário:** "criar o plano de implementação dessas specs e implementar inline" — ou seja, esta sessão termina em **specs escritas, revisadas e aprovadas**; a próxima deve ir direto pra `superpowers:writing-plans` → execução inline (não subagent-driven), TDD, do jeito que D1 foi feito nesta sessão (ver §5).

---

## 1. O que ler primeiro

1. `docs/superpowers/specs/2026-09-14-document-signup-to-market-design.md` (D4) — CPF/CNPJ migra do cadastro pra criação da loja.
2. `docs/superpowers/specs/2026-09-14-product-analytics-posthog-design.md` (D9) — PostHog, frontend + backend.

Os dois já foram apresentados ao usuário em chat e ele pediu handoff pra próxima sessão implementar — **mas confirme que ele não pediu mudança nos specs antes de começar a plan-ar**. Se a conversa desde então tiver alguma correção, os specs em disco são a fonte de verdade, não a memória desta sessão.

**Os dois specs citam arquivo:linha do código em 2026-09-14. Releia o código real antes de planejar** — linhas podem ter mudado.

## 2. Decisão de produto ainda aberta (D4)

O spec de D4, seção 6, deixa em aberto: quando o dono tem mais de uma loja com documentos diferentes e não tem CPF pessoal cadastrado, qual documento vira o padrão no checkout de cobrança recorrente? O spec propõe "a loja mais antiga" mas **não foi confirmado pelo usuário**. Pergunte antes de fechar essa parte específica do plano — o resto de D4 não depende dessa resposta.

## 3. Estado atual dos repos (ao final desta sessão)

Ambos em `main`, limpos, sem branch pendente:

**backend-marketfy** (`git log --oneline -3`):
```
7a03f7a Merge pull request #4 from carlos-edu2367/docs/d4-d9-specs
a330951 docs(specs): D4 (documento CPF/CNPJ) e D9 (analytics PostHog)
81dc1cd Merge pull request #3 from carlos-edu2367/feature/finance-only-on-pro
```

**marketfy-frontend** (`git log --oneline -3`):
```
bdcd659 Merge pull request #5 from carlos-edu2367/feature/finance-only-on-pro
e9baca1 feat(admin): manage Plan.includes_finance (D1)
4fcec0f Merge pull request #4 from carlos-edu2367/feature/conversion-round2-frontend-lot-b
```

Nesta sessão foram implementados e já estão em produção: a rodada 2 completa do plano de conversão (handoff original) e D1 (Financeiro só no PRO). Ver `docs/superpowers/plans/2026-09-14-conversion-round2-backend.md` e o par no frontend pra contexto de como o produto chegou até aqui.

## 4. Deploy

- **Backend:** Railway, auto-deploy no push pra `main` de `backend-marketfy` (dois serviços: `backend-marketfy` API em `api-marketfy.neectify.com` e `worker`). Confirmar deploy com `gh api repos/carlos-edu2367/backend-marketfy/commits/main/status`.
- **Frontend:** Vercel, auto-deploy no push pra `main` de `marketfy-frontend`. Confirmar com `gh api repos/carlos-edu2367/marketfy-frontend/commits/main/status`.
- **Banco de produção:** Postgres no Railway. **A connection string não está neste documento nem em nenhum arquivo do repo — não deve ficar.** Peça pro usuário de novo quando for aplicar uma migration em produção (aconteceu 3x nesta sessão, ele forneceu na hora). Nunca a escreva em arquivo versionado.
- Fluxo usado nesta sessão pra aplicar migration em produção: branch com a migration → `alembic upgrade head` local, com `DATABASE_URL` apontando pro Postgres do Railway via env var de shell (nunca hardcoded) → verificar antes/depois com uma query direta (`asyncpg`) → só then merge do PR.

## 5. Como esta sessão trabalhou (pra manter consistência)

- **TDD sempre:** teste primeiro, rodar e confirmar que falha, implementar, rodar e confirmar que passa. Nenhuma exceção nesta sessão.
- **Branch por task/feature**, nunca commit direto em `main`. PR mesmo quando é só o usuário revisando (ele aprova rápido, mas o histórico de PR importa).
- **Baseline sempre medido antes de mudar algo** (`python -m pytest tests/unit -q` no backend, `npx vitest run` no frontend) e **suíte inteira reconferida no final de cada task**, não só o teste novo.
- Teste de rota nova no backend: padrão de `app.dependency_overrides` (ver `tests/unit/test_finance_router_plan_gate.py` pra gate de feature, `tests/unit/test_tax_rule_routes.py` pro truque de override de `require_market_access(...)` via `dependency.call.__name__ == "_dep"`).
- `tests/conftest.py` já tem um shim `@compiles(postgresql.UUID, "sqlite")` — qualquer teste que rode `Base.metadata.create_all` contra SQLite em memória já funciona, não precisa reinventar.
- Migrations: aditivas, sempre com `server_default` pra não quebrar dado existente; nunca migrar/reescrever dado histórico numa migration de schema.
- `docs/agent_memory/` é onde ficam achados técnicos reusáveis (ver `docs/agent_memory/plans-and-paywall-findings.md`) — some ali se encontrar algo que a próxima sessão depois desta precisa saber.

## 6. Fatos de produção a não esquecer

- Existe **cliente real** no plano Básico (`id c88f9742-8a6b-42bd-8b4d-c233a7bb1230`, nome armazenado com mojibake — "BÃ¡sico" — bug de encoding pré-existente, não mexi). Esse plano já está com `includes_finance = false` desde esta sessão — intencional, não é bug se aparecer num ticket de suporte.
- `Plan.includes_finance` (D1), `Plan.description`/`is_recommended`/`display_order` (rodada 2) e `Plan.fiscal_monthly_limit` editável (rodada 2) já existem e estão em produção — D4/D9 não interagem com esses campos, mas qualquer novo campo em `Plan` deve seguir o mesmo padrão (migration aditiva com `server_default`, DTO opcional, admin service, `_plan_to_response` em `admin.py`).

## 7. Próximo passo literal

Invocar `superpowers:writing-plans` pros dois specs (podem virar um plano só ou dois — D4 e D9 não dependem um do outro, então dois planos separados executados em paralelo/sequência é razoável), depois `superpowers:executing-plans` ou `superpowers:subagent-driven-development` conforme o usuário preferir — nesta sessão ele pediu inline.
