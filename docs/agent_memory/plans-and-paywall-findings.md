# Planos e paywall — achados (2026-09-14)

- `POST /identity/plans/{id}/subscribe` ativava plano sem cobrança para qualquer dono sem BillingSubscription; restrita a admin (rodada 2 de conversão, PR separado `fix/legacy-plan-subscribe-guard`).
- `PlanAccessService._fallback_from_user` libera acesso por `users.plan_id` quando não existe assinatura local — qualquer caminho que grave `plan_id` sem criar assinatura é um bypass em potencial.
- Limite `0` em `max_markets`/`max_terminals`/`fiscal_monthly_limit` significa "não incluído" (bloqueia), não "ilimitado".
- `SQLAlchemyPlanRepository.save` precisa mapear campo a campo; campo novo em `Plan` sem linha no `save` é silenciosamente descartado.
- `UserModel` não tem coluna `cnpj`; documento cadastrado = CPF.
- `GET /identity/plans` é público e passa por `select_public_plans` (ativos, `pago`/`trial`).
- Modelos usam `sqlalchemy.dialects.postgresql.UUID`, que não compila DDL para SQLite. Qualquer teste que rode `Base.metadata.create_all` contra `sqlite+aiosqlite` precisa do shim `@compiles(UUID, "sqlite")` registrado em `tests/conftest.py` (adicionado nesta rodada — corrigiu 3 testes de `test_billing_invoice_repo.py` que já estavam quebrados antes desta rodada).
- Migration nova (`20260914_0021`) não foi validada contra Postgres real — só contra SQLite in-memory nos testes. Rodar `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` em staging antes do deploy.
