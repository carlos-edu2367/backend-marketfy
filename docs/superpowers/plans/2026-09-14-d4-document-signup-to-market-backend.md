# D4 — Documento fiscal migra do cadastro para a loja (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar `User.cpf` opcional no cadastro, aceitar CPF ou CNPJ na criação da loja (com validação de dígito verificador), e fazer `billing_document` cair para o documento da loja mais antiga do dono quando não houver CPF pessoal — sem quebrar nenhuma conta existente.

**Architecture:** Mudança de domínio pura (sem migration — as colunas já suportam isso). `User.cpf: CPF` vira `Optional[CPF]`; `Market.document: CNPJ` vira `str` simples (CPF e CNPJ têm formatos de exibição diferentes, então guardamos dígitos crus e formatamos na borda). Duas novas funções puras de validação (`validate_cpf`, `validate_document`) em `domain/validators.py`, espelhando `validate_cnpj` que já existe. `billing_document.registered_document()` ganha um parâmetro opcional `markets` para o fallback.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2.5, SQLAlchemy async, pytest + pytest-asyncio (SQLite em memória via aiosqlite nos testes de repositório, shim `@compiles(postgresql.UUID, "sqlite")` já em `tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-09-14-document-signup-to-market-design.md`. Decisão de produto do §6 fechada com o usuário em 2026-09-14: quando o dono não tem CPF pessoal e tem 2+ lojas, usa a **loja mais antiga** (`created_at` mínimo) como documento padrão no checkout recorrente.

## Global Constraints

- Testes seguem o padrão existente: `sys.path.append(<repo>/app)` no topo do arquivo, imports sem prefixo `app.` (ex.: `from domain.validators import validate_cpf`).
- Rotas testadas com `fastapi.testclient.TestClient` e `app.dependency_overrides[...]`, sempre limpando os overrides no `finally`.
- Nenhuma migration — `UserModel.cpf` já é `nullable=True`, `MarketModel.document` já é `String` genérico.
- Nenhuma mudança em `FiscalTenantConfig.cnpj` ou no wizard fiscal (`FiscalOnboardingWizard.jsx`, `fiscal_onboarding_service.py`) — fora de escopo, CNPJ diferente com validação própria.
- Contas existentes com `User.cpf` preenchido continuam exatamente como estão — `registered_document()` prioriza `user.cpf` quando presente.
- Mensagens de erro para o usuário final em português.
- Commits terminam com `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

## Evidência levantada relendo o código em 2026-09-14 (linhas do spec conferidas, uma discrepância crítica encontrada)

| Achado | Evidência |
|---|---|
| Linhas do spec §5 batem com o código atual | Conferido `identity.py` (User:68 `cpf: CPF`, Market:108 `document: CNPJ`), `dtos.py` (UserCreateDTO:22 `cpf: str`, MarketCreateDTO:79 `document: str`), `identity_service.py` (register_user:42 `cpf=CPF(dto.cpf)`, create_market:85 `document=CNPJ(dto.document)`), `sqlalchemy_repos.py` (Market.save:210 `model.document = market.document.value`, `_to_entity`:220 `document=CNPJ(m.document)`), `billing_document.py` (arquivo inteiro), `billing.py`:134-137, `auth.py`:225. Nada mudou desde que o spec foi escrito (só commits de docs depois). |
| **CRÍTICO — não estava no spec:** dois loaders de `User` vão crashar com `cpf=None` | `SQLAlchemyUserRepository._to_entity` (`sqlalchemy_repos.py:94`, usado por `get_by_id`/`get_by_email` — **é o caminho de `get_current_user`, ou seja, toda rota autenticada**) e `SQLAlchemyTicketRepository._user_model_to_entity` (`sqlalchemy_repos.py:983`) fazem `cpf=CPF(m.cpf)` **sem checar `None`**. `CPF.__post_init__` roda `re.sub(r'\D', '', self.value)` — com `self.value=None` isso levanta `TypeError`, não `ValidationException`. Sem corrigir isso, o primeiro usuário que se cadastrar sem CPF vai receber 500 em `/auth/me` e em qualquer rota protegida. `SQLAlchemyCustomerRepository._to_entity` (`sqlalchemy_repos.py:855`) já faz `cpf=CPF(m.cpf) if m.cpf else None` corretamente — é a `Customer` (cliente fiado), não `User`, mas confirma que esse é o padrão certo a seguir. |
| `Market.document` não é usado em 2 pontos, e sim mais — mas o resto é `Market(document=CNPJ(` só em `identity_service.py:85` | Confirmado por busca: nenhum teste constrói `Market(document=CNPJ(...))` diretamente. `Market(` aparece só em `identity_service.py` (produção) e potencialmente em fixtures de teste que passam string — sem risco de tipagem. |
| Teste espelho de `validate_cnpj` já existe (spec citou o arquivo errado) | `tests/unit/test_fiscal_onboarding.py:480` (`test_validate_cnpj_mathematically`), não `test_tax_rule_calculator.py`. CNPJ de teste válido reaproveitado aqui: `"12345678000195"`. CPF de teste válido calculado e conferido manualmente com o mesmo algoritmo de pesos do spec: `"11144477735"`. |
| `mask_document()` só sabe mascarar CPF (11 dígitos) | `billing_document.py`: `if not digits or len(digits) != 11: return None`. Depois de D4, `registered_document()` pode devolver um CNPJ (14 dígitos) vindo da loja — `mask_document` precisa saber mascarar os dois formatos, senão `/auth/me` simplesmente não mostra `document_masked` para quem caiu no fallback de loja. Não estava no spec §5 explicitamente — adicionado aqui. |
| `resolve_billing_document()` também precisa do parâmetro `markets` | O spec §5 só menciona `registered_document()` ganhando `markets`, mas `billing.py:135` chama `resolve_billing_document(dto.document, current_user)`, que é quem de fato decide o documento do checkout recorrente. Sem propagar `markets` por `resolve_billing_document`, o fallback nunca chega no router. |
| `list_by_owner` já existe no repositório de mercados | `MarketRepositoryInterface.list_by_owner` (`interfaces.py:45`), implementado em `SQLAlchemyMarketRepository.list_by_owner` (`sqlalchemy_repos.py:186`) e já usado em `identity_service.get_user_markets`. Nenhum método novo necessário. |
| Bônus: mudança corrige um bug de exibição pré-existente no cupom fiscal | `Market.document` hoje serializa via FastAPI (sem `response_model` em `GET/POST /identity/markets`) como `{"value": "..."}` (dataclass `CNPJ` aninhado, `dataclasses.asdict`). `Receipt.jsx:124` já lê `marketInfo.document` como string simples — hoje provavelmente renderiza `CNPJ: [object Object]`. Confirmado que nenhum código do frontend faz `.document.value` (busca completa em `src/`). Trocar `Market.document` para `str` corrige esse bug de graça — sem tarefa extra, é consequência natural da Task 5. |

## Ordem de execução e deploy

1. Tasks 1 → 7 em sequência, um PR (branch única, TDD, commit por task). Task 2 é a mais sensível (contém o fix crítico) — não pular a suíte completa depois dela.
2. Rodar a suíte inteira (`python -m pytest tests/unit -q`) ao final de cada task, não só o teste novo.
3. Nenhuma migration a aplicar em produção — pode mergear e dar deploy normal (auto-deploy Railway no push pra `main`).
4. Companion: `frontend/docs/superpowers/plans/2026-09-14-d4-document-signup-to-market-frontend.md`. Frontend consome os DTOs relaxados (cpf opcional, document flexível) — pode ir depois do deploy do backend, backend é retrocompatível com o frontend atual (que ainda manda CPF + CNPJ de 14 dígitos).

---

### Task 1: `validate_cpf` e `validate_document` em `domain/validators.py`

**Files:**
- Modify: `app/domain/validators.py`
- Test: `tests/unit/test_validators.py` (novo arquivo — não existe teste unitário dedicado a `validators.py` hoje, só o espelho de `validate_cnpj` em `test_fiscal_onboarding.py:480`)

**Interfaces:**
- Produces: `validate_cpf(cpf: str) -> bool`, `validate_document(digits: str) -> bool` — usados por `dtos.py` (Task 3).

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/unit/test_validators.py
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.validators import validate_cpf, validate_document


def test_validate_cpf_accepts_known_valid_cpf():
    assert validate_cpf("11144477735") is True
    assert validate_cpf("111.444.777-35") is True


def test_validate_cpf_rejects_wrong_check_digit():
    assert validate_cpf("11144477734") is False


def test_validate_cpf_rejects_all_same_digits():
    assert validate_cpf("11111111111") is False


def test_validate_cpf_rejects_wrong_length():
    assert validate_cpf("123") is False
    assert validate_cpf("123456789012") is False


def test_validate_document_accepts_valid_cpf_11_digits():
    assert validate_document("11144477735") is True


def test_validate_document_accepts_valid_cnpj_14_digits():
    assert validate_document("12345678000195") is True


def test_validate_document_rejects_wrong_length():
    assert validate_document("123456789012") is False  # 12 dígitos


def test_validate_document_rejects_cpf_with_bad_checksum():
    assert validate_document("11144477734") is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_validators.py -v`
Expected: FAIL com `ImportError: cannot import name 'validate_cpf'`

- [ ] **Step 3: Implementar `validate_cpf` e `validate_document`**

Adicionar ao final de `app/domain/validators.py`:

```python
def validate_cpf(cpf: str) -> bool:
    """
    Executa a validação matemática detalhada de um CPF (dígitos verificadores).
    Retorna True se o CPF for válido, False caso contrário.
    """
    cpf_digits = re.sub(r"\D", "", cpf)

    if len(cpf_digits) != 11:
        return False

    if len(set(cpf_digits)) == 1:
        return False

    weights_1 = [10, 9, 8, 7, 6, 5, 4, 3, 2]
    sum_1 = sum(int(cpf_digits[i]) * weights_1[i] for i in range(9))
    remainder_1 = sum_1 % 11
    digit_1 = 0 if remainder_1 < 2 else 11 - remainder_1

    if int(cpf_digits[9]) != digit_1:
        return False

    weights_2 = [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]
    sum_2 = sum(int(cpf_digits[i]) * weights_2[i] for i in range(10))
    remainder_2 = sum_2 % 11
    digit_2 = 0 if remainder_2 < 2 else 11 - remainder_2

    if int(cpf_digits[10]) != digit_2:
        return False

    return True


def validate_document(digits: str) -> bool:
    """
    Despacha para validate_cpf (11 dígitos) ou validate_cnpj (14 dígitos)
    conforme o tamanho, depois de limpar caracteres não numéricos.
    Retorna False para qualquer outro tamanho.
    """
    clean = re.sub(r"\D", "", digits)
    if len(clean) == 11:
        return validate_cpf(clean)
    if len(clean) == 14:
        return validate_cnpj(clean)
    return False
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_validators.py -v`
Expected: PASS (8 testes)

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS, nenhuma regressão

- [ ] **Step 6: Commit**

```bash
git add app/domain/validators.py tests/unit/test_validators.py
git commit -m "feat(validators): add validate_cpf and validate_document (D4)"
```

---

### Task 2: `User.cpf` opcional e `Market.document` vira `str` — com o fix crítico de carregamento

**Files:**
- Modify: `app/domain/identity.py`
- Modify: `app/infra/repositories/sqlalchemy_repos.py` (`SQLAlchemyUserRepository._to_entity`, `SQLAlchemyTicketRepository._user_model_to_entity`)
- Test: `tests/unit/test_validators.py` → não; testes vão em `tests/unit/test_identity_optional_cpf.py` (novo)

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `User.cpf: Optional[CPF]`, `Market.document: str` — consumidos pelas Tasks 3-8.

- [ ] **Step 1: Escrever o teste que falha (domínio)**

```python
# tests/unit/test_identity_optional_cpf.py
from __future__ import annotations

import os
import sys
import uuid

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.identity import User, Market, UserRole
from domain.shared import Email


def test_user_can_be_created_without_cpf():
    user = User(name="Ana", email=Email("ana@t.com"), cpf=None, password_hash="x", role=UserRole.OWNER)
    assert user.cpf is None


def test_market_document_is_a_plain_string():
    market = Market(owner_id=uuid.uuid4(), name="Loja", document="12345678000195", address="Rua X")
    assert market.document == "12345678000195"
    assert isinstance(market.document, str)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_identity_optional_cpf.py -v`
Expected: FAIL — `User(...)` sem passar `cpf` explicitamente já funcionaria hoje só se `cpf=None` for aceito pelo dataclass; hoje `cpf: CPF` não tem default e não aceita `None` como valor válido de tipo (embora Python não valide tipo em runtime, o teste de `Market.document == "12345678000195"` com `document: CNPJ` também "passaria" em runtime porque dataclasses não checam tipo — então o teste real que falha é o `_to_entity` abaixo). Ainda assim, escreva e rode — serve de documentação executável da mudança de contrato.

- [ ] **Step 3: Editar `domain/identity.py`**

`app/domain/identity.py:68` — trocar:
```python
    cpf: CPF
```
por:
```python
    cpf: Optional[CPF] = None
```

`app/domain/identity.py:108` — trocar:
```python
    document: CNPJ
```
por:
```python
    document: str
```

Nota: `User` é `@dataclass` com vários campos depois de `cpf` sem default (ex.: `password_hash`, `role`) — como `cpf` agora tem `= None`, todo campo declarado *depois* dele no dataclass também precisa ter default, ou o Python levanta `TypeError: non-default argument follows default argument` na definição da classe. Antes de aplicar o replace, ler `app/domain/identity.py:65-90` por completo e, se algum campo depois de `cpf` não tiver default, mover `cpf: Optional[CPF] = None` para o final da lista de campos do dataclass (mantendo os demais na mesma ordem) em vez de só trocar o tipo in-place. Mesma checagem vale para `Market.document` na linha 108.

- [ ] **Step 4: Corrigir os dois loaders que crashariam com `cpf=None`**

`app/infra/repositories/sqlalchemy_repos.py` — em `SQLAlchemyUserRepository._to_entity` (perto da linha 94):
```python
            cpf=CPF(m.cpf),
```
vira:
```python
            cpf=CPF(m.cpf) if m.cpf else None,
```

Em `SQLAlchemyTicketRepository._user_model_to_entity` (perto da linha 983), a mesma troca:
```python
            cpf=CPF(m.cpf),
```
vira:
```python
            cpf=CPF(m.cpf) if m.cpf else None,
```

- [ ] **Step 5: Escrever o teste que prova o fix (o teste que realmente importa nesta task)**

Adicionar em `tests/unit/test_identity_optional_cpf.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from infra.database.models import Base, UserModel
from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository


@pytest.mark.asyncio
async def test_user_repository_loads_a_user_with_null_cpf_without_crashing():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        model = UserModel(
            name="Ana", email="ana@t.com", cpf=None, password_hash="x", role="owner",
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)

        repo = SQLAlchemyUserRepository(session)
        loaded = await repo.get_by_id(model.id)

        assert loaded is not None
        assert loaded.cpf is None
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_identity_optional_cpf.py -v`
Expected: PASS. Sem o Step 4, este teste falha com `TypeError` dentro de `CPF.__post_init__` — é a prova de que o fix é necessário.

- [ ] **Step 7: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS. Preste atenção especial em qualquer teste que carregue `User` ou tickets do banco — é exatamente o caminho tocado.

- [ ] **Step 8: Commit**

```bash
git add app/domain/identity.py app/infra/repositories/sqlalchemy_repos.py tests/unit/test_identity_optional_cpf.py
git commit -m "fix(identity): make User.cpf optional and Market.document a plain string (D4)

Also fixes two User loaders (SQLAlchemyUserRepository._to_entity,
SQLAlchemyTicketRepository._user_model_to_entity) that would raise
TypeError on cpf=None — found while implementing D4, not in the
original spec."
```

---

### Task 3: DTOs — `UserCreateDTO.cpf` opcional, `MarketCreateDTO.document` validado

**Files:**
- Modify: `app/application/dtos.py`
- Test: `tests/unit/test_identity_dtos.py` (novo)

**Interfaces:**
- Consumes: `validate_document` de `domain/validators.py` (Task 1).
- Produces: `UserCreateDTO.cpf: Optional[str] = None`, `MarketCreateDTO` validando `document` — consumidos pela Task 4.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/unit/test_identity_dtos.py
from __future__ import annotations

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from pydantic import ValidationError

from application.dtos import UserCreateDTO, MarketCreateDTO


def test_user_create_dto_accepts_missing_cpf():
    dto = UserCreateDTO(name="Ana", email="ana@t.com", password="segredo1")
    assert dto.cpf is None


def test_user_create_dto_still_accepts_cpf_if_sent():
    dto = UserCreateDTO(name="Ana", email="ana@t.com", cpf="11144477735", password="segredo1")
    assert dto.cpf == "11144477735"


def test_market_create_dto_accepts_valid_cpf_as_document():
    dto = MarketCreateDTO(name="Loja", document="111.444.777-35", address="Rua X")
    assert dto.document == "111.444.777-35"


def test_market_create_dto_accepts_valid_cnpj_as_document():
    dto = MarketCreateDTO(name="Loja", document="12.345.678/0001-95", address="Rua X")
    assert dto.document == "12.345.678/0001-95"


def test_market_create_dto_rejects_invalid_document():
    with pytest.raises(ValidationError):
        MarketCreateDTO(name="Loja", document="123", address="Rua X")


def test_market_create_dto_rejects_document_with_bad_checksum():
    with pytest.raises(ValidationError):
        MarketCreateDTO(name="Loja", document="111.444.777-34", address="Rua X")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_identity_dtos.py -v`
Expected: FAIL — `UserCreateDTO` exige `cpf` (campo obrigatório) e `MarketCreateDTO` não valida nada além do `min(14)` do frontend (não há validação no backend hoje, então `test_market_create_dto_rejects_invalid_document` falha por não levantar `ValidationError`).

- [ ] **Step 3: Editar `dtos.py`**

Adicionar o import no topo (`app/application/dtos.py:1`, junto dos imports existentes):
```python
from domain.validators import validate_document
```

`UserCreateDTO` (perto da linha 22) — trocar:
```python
    cpf: str
```
por:
```python
    cpf: Optional[str] = None
```

`MarketCreateDTO` (perto da linha 78-80) — trocar:
```python
class MarketCreateDTO(BaseModel):
    name: str
    document: str  # CNPJ
    address: str
```
por:
```python
class MarketCreateDTO(BaseModel):
    name: str
    document: str  # CPF ou CNPJ, validado por dígito verificador
    address: str

    @field_validator('document')
    @classmethod
    def validate_document_checksum(cls, v: str) -> str:
        if not validate_document(v):
            raise ValueError('CPF ou CNPJ inválido.')
        return v
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_identity_dtos.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS — atenção a qualquer teste que hoje monte `UserCreateDTO` ou `MarketCreateDTO` com documento inválido só por tamanho (ex.: `"00000000000000"`, todos zeros) esperando sucesso; esses agora falham a validação de checksum corretamente e o teste precisa ser ajustado para usar um documento matematicamente válido.

- [ ] **Step 6: Commit**

```bash
git add app/application/dtos.py tests/unit/test_identity_dtos.py
git commit -m "feat(dtos): make UserCreateDTO.cpf optional, validate MarketCreateDTO.document checksum (D4)"
```

---

### Task 4: `identity_service.py` — `register_user` e `create_market`

**Files:**
- Modify: `app/application/services/identity_service.py`
- Test: `tests/unit/test_identity_service_document.py` (novo)

**Interfaces:**
- Consumes: `User.cpf: Optional[CPF]`, `Market.document: str` (Task 2); `MarketCreateDTO` validado (Task 3).
- Produces: `IdentityService.register_user` sem CPF obrigatório; `IdentityService.create_market` aceitando CPF ou CNPJ — consumidos pela Task 5 (persistência) e pelos testes de rota.

- [ ] **Step 1: Escrever os testes que falham**

Ver primeiro como o serviço é testado hoje — confira `tests/unit/` por um teste existente de `IdentityService` para reaproveitar fakes de repositório (ex.: `test_identity_legacy_subscribe_guard.py`). Se não houver um fake de `UserRepositoryInterface`/`MarketRepositoryInterface` pronto reaproveitável, criar um mínimo local no novo arquivo:

```python
# tests/unit/test_identity_service_document.py
from __future__ import annotations

import os
import sys
import uuid

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from application.dtos import UserCreateDTO, MarketCreateDTO
from application.services.identity_service import IdentityService
from domain.identity import User, UserRole


class FakeUserRepo:
    def __init__(self):
        self.saved = None

    async def get_by_email(self, email):
        return None

    async def save(self, user, commit=True):
        self.saved = user
        user.id = uuid.uuid4()
        return user

    async def get_by_id(self, user_id):
        return self.saved


class FakeMarketRepo:
    def __init__(self):
        self.saved = None

    async def count_by_owner(self, owner_id):
        return 0

    async def save(self, market, commit=True):
        self.saved = market
        market.id = uuid.uuid4()
        return market


class FakePlanRepo:
    def __init__(self, plan):
        self.plan = plan

    async def get_by_id(self, plan_id):
        return self.plan


def _owner_with_plan(plan_id):
    user = User(name="Ana", email=None, cpf=None, password_hash="x", role=UserRole.OWNER)
    user.id = uuid.uuid4()
    user.plan_id = plan_id
    return user


@pytest.mark.asyncio
async def test_register_user_without_cpf_succeeds():
    user_repo = FakeUserRepo()
    service = IdentityService(user_repo=user_repo, market_repo=FakeMarketRepo(), plan_repo=None, hasher=lambda p: p)
    dto = UserCreateDTO(name="Ana", email="ana@t.com", password="segredo1")

    await service.register_user(dto)

    assert user_repo.saved.cpf is None


@pytest.mark.asyncio
async def test_create_market_with_valid_cpf_stores_11_digits():
    from domain.shared import Plan  # ajustar import conforme o construtor real de Plan usado no plan_access

    plan_id = uuid.uuid4()

    class FakePlan:
        id = plan_id
        name = "Basico"
        max_markets = 5
        def is_limit_reached(self, current, kind):
            return False

    user_repo = FakeUserRepo()
    owner = _owner_with_plan(plan_id)
    user_repo.saved = owner
    market_repo = FakeMarketRepo()
    service = IdentityService(
        user_repo=user_repo, market_repo=market_repo, plan_repo=FakePlanRepo(FakePlan()), hasher=lambda p: p,
    )
    dto = MarketCreateDTO(name="Loja", document="111.444.777-35", address="Rua X")

    market = await service.create_market(owner.id, dto)

    assert market.document == "11144477735"


@pytest.mark.asyncio
async def test_create_market_with_valid_cnpj_stores_14_digits():
    plan_id = uuid.uuid4()

    class FakePlan:
        id = plan_id
        name = "Basico"
        max_markets = 5
        def is_limit_reached(self, current, kind):
            return False

    user_repo = FakeUserRepo()
    owner = _owner_with_plan(plan_id)
    user_repo.saved = owner
    market_repo = FakeMarketRepo()
    service = IdentityService(
        user_repo=user_repo, market_repo=market_repo, plan_repo=FakePlanRepo(FakePlan()), hasher=lambda p: p,
    )
    dto = MarketCreateDTO(name="Loja", document="12.345.678/0001-95", address="Rua X")

    market = await service.create_market(owner.id, dto)

    assert market.document == "12345678000195"
```

Antes de rodar: abrir `app/application/services/identity_service.py` e conferir o `__init__` real de `IdentityService` (nomes exatos dos parâmetros do construtor) e ajustar as chamadas acima se os nomes dos kwargs (`user_repo`, `market_repo`, `plan_repo`, `hasher`) diferirem — o spec não documentou o construtor, então isso precisa ser lido do arquivo real antes de finalizar o teste. Remover o import solto de `domain.shared.Plan` se não existir — foi deixado como lembrete de checar, não é necessário para o teste.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_identity_service_document.py -v`
Expected: FAIL — `register_user` hoje faz `cpf=CPF(dto.cpf)` incondicional (quebra com `dto.cpf=None`); `create_market` hoje faz `document=CNPJ(dto.document)` (quebra ou produz um objeto `CNPJ`, não a string `"11144477735"`).

- [ ] **Step 3: Editar `identity_service.py`**

`register_user` (perto da linha 39-44) — trocar:
```python
        new_user = User(
            name=dto.name,
            email=Email(dto.email),
            cpf=CPF(dto.cpf),
            password_hash=password_hash,
            role=UserRole.OWNER 
```
por:
```python
        new_user = User(
            name=dto.name,
            email=Email(dto.email),
            cpf=CPF(dto.cpf) if dto.cpf else None,
            password_hash=password_hash,
            role=UserRole.OWNER 
```

`create_market` (perto da linha 82-88) — trocar:
```python
        new_market = Market(
            owner_id=user.id,
            name=dto.name,
            document=CNPJ(dto.document),
            address=dto.address,
            active=True
        )
```
por:
```python
        import re as _re
        new_market = Market(
            owner_id=user.id,
            name=dto.name,
            document=_re.sub(r'\D', '', dto.document),
            address=dto.address,
            active=True
        )
```

Preferir mover o `import re` para o topo do arquivo (junto dos outros imports) em vez de inline, se o arquivo ainda não importar `re` — checar antes de editar.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_identity_service_document.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/application/services/identity_service.py tests/unit/test_identity_service_document.py
git commit -m "feat(identity): register_user accepts missing cpf, create_market accepts CPF or CNPJ (D4)"
```

---

### Task 5: `sqlalchemy_repos.py` — persistência do `Market.document` como string

**Files:**
- Modify: `app/infra/repositories/sqlalchemy_repos.py` (`SQLAlchemyMarketRepository.save`, `_to_entity`)
- Test: `tests/unit/test_market_repository_document.py` (novo)

**Interfaces:**
- Consumes: `Market.document: str` (Task 2).
- Produces: round-trip correto de `document` (string crua, sem `.value`) — consumido pela API (Task 4 já cria o `Market`, esta task garante que salvar/recarregar preserva o valor).

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/unit/test_market_repository_document.py
from __future__ import annotations

import os
import sys
import uuid

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from infra.database.models import Base
from infra.repositories.sqlalchemy_repos import SQLAlchemyMarketRepository
from domain.identity import Market


@pytest.mark.asyncio
async def test_market_document_round_trips_as_plain_string_cpf_length():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        repo = SQLAlchemyMarketRepository(session)
        market = Market(owner_id=uuid.uuid4(), name="Loja", document="11144477735", address="Rua X")

        saved = await repo.save(market)
        assert saved.document == "11144477735"

        reloaded = repo._to_entity(await session.get(type(await session.get.__self__.get_bind() and None) or object, saved.id)) if False else None
        # recarrega via query direta ao invés do helper acima (evita depender de internals do engine)
        from infra.database.models import MarketModel
        model = await session.get(MarketModel, saved.id)
        reloaded = repo._to_entity(model)
        assert reloaded.document == "11144477735"


@pytest.mark.asyncio
async def test_market_document_round_trips_as_plain_string_cnpj_length():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        repo = SQLAlchemyMarketRepository(session)
        market = Market(owner_id=uuid.uuid4(), name="Loja", document="12345678000195", address="Rua X")

        saved = await repo.save(market)

        from infra.database.models import MarketModel
        model = await session.get(MarketModel, saved.id)
        reloaded = repo._to_entity(model)
        assert reloaded.document == "12345678000195"
```

Antes de finalizar: apagar a linha comentada/confusa `reloaded = repo._to_entity(...) if False else None` do primeiro teste — foi deixada por engano na primeira redação; o padrão limpo (buscar o `MarketModel` via `session.get` e passar pra `_to_entity`) é o que os dois testes já usam depois dela.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_market_repository_document.py -v`
Expected: FAIL — `save()` hoje faz `model.document = market.document.value` (uma `str` não tem atributo `.value`, `AttributeError`); `_to_entity` hoje faz `document=CNPJ(m.document)`, o que devolveria um objeto `CNPJ`, não a string igual ao que foi salvo.

- [ ] **Step 3: Editar `SQLAlchemyMarketRepository.save` e `_to_entity`**

Em `save()` (perto da linha 210) — trocar:
```python
        model.document = market.document.value
```
por:
```python
        model.document = market.document
```

Em `_to_entity()` (perto da linha 220) — trocar:
```python
        market = Market(
            owner_id=m.owner_id,
            name=m.name,
            document=CNPJ(m.document),
            address=m.address,
            active=m.is_active
        )
```
por:
```python
        market = Market(
            owner_id=m.owner_id,
            name=m.name,
            document=m.document,
            address=m.address,
            active=m.is_active
        )
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_market_repository_document.py -v`
Expected: PASS (2 testes)

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/infra/repositories/sqlalchemy_repos.py tests/unit/test_market_repository_document.py
git commit -m "fix(market): persist and load document as a plain string, not CNPJ (D4)"
```

---

### Task 6: `billing_document.py` — fallback pra loja mais antiga e máscara de CNPJ

**Files:**
- Modify: `app/application/services/billing_document.py`
- Modify: `tests/unit/test_billing_document.py` (arquivo existente — estender, não recriar)

**Interfaces:**
- Consumes: `Market.document: str`, `Market.created_at` (já existe em `Entity`, confirmado em `_to_entity` da Task 5).
- Produces: `registered_document(user, markets=None)`, `resolve_billing_document(provided, user, markets=None)`, `mask_document(digits)` cobrindo CPF e CNPJ — consumidos pelas Tasks 7 e 8.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/unit/test_billing_document.py` (mantendo os testes existentes intactos):

```python
def _market(document, created_at):
    return SimpleNamespace(document=document, created_at=created_at)


def test_registered_document_falls_back_to_oldest_market_without_personal_cpf():
    from datetime import datetime
    markets = [
        _market("12345678000195", datetime(2024, 6, 1)),
        _market("98765432000110", datetime(2023, 1, 1)),  # mais antiga
    ]
    assert registered_document(_user(cpf=None), markets=markets) == "98765432000110"


def test_registered_document_prefers_personal_cpf_over_markets():
    from datetime import datetime
    markets = [_market("12345678000195", datetime(2023, 1, 1))]
    assert registered_document(_user(), markets=markets) == "12345678901"


def test_registered_document_returns_none_without_cpf_or_markets():
    assert registered_document(_user(cpf=None), markets=[]) is None
    assert registered_document(_user(cpf=None)) is None


def test_resolve_billing_document_propagates_markets_fallback():
    from datetime import datetime
    markets = [_market("12345678000195", datetime(2023, 1, 1))]
    assert resolve_billing_document(None, _user(cpf=None), markets=markets) == "12345678000195"


def test_mask_document_masks_cnpj_keeping_only_middle_digits():
    assert mask_document("12345678000195") == "**.345.678/****-**"


def test_mask_document_still_masks_cpf():
    assert mask_document("12345678901") == "***.456.789-**"
```

Adicionar `SimpleNamespace` (já importado) e o novo `_market` helper. Nenhum import novo necessário no arquivo de teste.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_billing_document.py -v`
Expected: FAIL — `registered_document` não aceita `markets=`; `resolve_billing_document` idem; `mask_document("12345678000195")` retorna `None` (só trata 11 dígitos).

- [ ] **Step 3: Reescrever `billing_document.py`**

```python
"""Documento (CPF/CNPJ) usado na cobrança, sem expor o número completo na API."""

import re
from typing import Any, Optional, Sequence


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _oldest_market_document(markets: Optional[Sequence[Any]]) -> Optional[str]:
    if not markets:
        return None
    oldest = min(markets, key=lambda m: m.created_at)
    digits = _digits(getattr(oldest, "document", None))
    return digits or None


def registered_document(user: Any, markets: Optional[Sequence[Any]] = None) -> Optional[str]:
    """CPF cadastrado do usuário; se ausente, cai para o documento da loja mais antiga."""
    cpf = getattr(user, "cpf", None)
    digits = _digits(getattr(cpf, "value", cpf))
    if len(digits) == 11:
        return digits
    return _oldest_market_document(markets)


def mask_document(digits: Optional[str]) -> Optional[str]:
    if not digits:
        return None
    if len(digits) == 11:
        return f"***.{digits[3:6]}.{digits[6:9]}-**"
    if len(digits) == 14:
        return f"**.{digits[2:5]}.{digits[5:8]}/****-**"
    return None


def resolve_billing_document(
    provided: Optional[str], user: Any, markets: Optional[Sequence[Any]] = None
) -> Optional[str]:
    """Documento digitado no checkout; se ausente, o documento cadastrado (CPF ou loja)."""
    return _digits(provided) or registered_document(user, markets=markets)
```

Nota sobre a máscara de CNPJ: a escolha é esconder a raiz (8 primeiros dígitos) e os dígitos verificadores, revelando só o bloco de filial (`0001` na maioria das lojas com CNPJ único) — segue o mesmo espírito da máscara de CPF já existente (esconde início e fim, revela o meio). Isso é uma decisão de design desta implementação, não do spec original; documentado aqui para quem revisar depois.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_billing_document.py -v`
Expected: PASS — todos os testes, os antigos (CPF-only) e os novos.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/application/services/billing_document.py tests/unit/test_billing_document.py
git commit -m "feat(billing): fall back to the owner's oldest market document when there's no personal CPF (D4)"
```

---

### Task 7: `billing.py` router — passar `markets` pro fallback no checkout recorrente

**Files:**
- Modify: `app/infra/web/routers/billing.py`
- Test: `tests/unit/test_billing_recurring_document_fallback.py` (novo)

**Interfaces:**
- Consumes: `resolve_billing_document(provided, user, markets)` (Task 6); `SQLAlchemyMarketRepository.list_by_owner` (já existe).

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/unit/test_billing_recurring_document_fallback.py
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from types import SimpleNamespace

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from fastapi.testclient import TestClient
import pytest

from infra.web.main import app
from infra.web.routers import billing as billing_router


class StubMarketRepo:
    def __init__(self, db):
        pass

    async def list_by_owner(self, owner_id):
        return [SimpleNamespace(document="12345678000195", created_at=datetime(2023, 1, 1))]


class StubRecurringService:
    def __init__(self, *args, **kwargs):
        pass

    async def contract(self, user, plan_id, subscription_type, document, idempotency_key):
        StubRecurringService.last_document = document
        return {"subscription_id": str(uuid.uuid4()), "job_id": "j1", "checkout_url": "https://pay"}


def _user_without_cpf():
    return SimpleNamespace(
        id=uuid.uuid4(), name="Ana", email="ana@t.com", role="owner",
        plan_id=uuid.uuid4(), plan_expiration=None, is_active=True, cpf=None,
    )


def test_recurring_subscribe_falls_back_to_oldest_market_document(monkeypatch):
    monkeypatch.setattr("infra.repositories.sqlalchemy_repos.SQLAlchemyMarketRepository", StubMarketRepo)
    monkeypatch.setattr("application.services.recurring_service.RecurringService", StubRecurringService)

    app.dependency_overrides[billing_router.get_current_user] = _user_without_cpf
    app.dependency_overrides[billing_router.get_db] = lambda: None
    app.dependency_overrides[billing_router.get_audit_service] = lambda: None

    try:
        response = TestClient(app).post(
            "/api/v1/billing/subscribe",
            json={"plan_id": str(uuid.uuid4()), "subscription_type": "monthly", "billing_mode": "recurring"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert StubRecurringService.last_document == "12345678000195"
```

Conferir antes de rodar: o prefixo real da rota (`/api/v1/billing` — confirmado em `test_billing_document.py` que usa `/api/v1/auth/me`, então o padrão de prefixo é `/api/v1/<router>`; ajustar se o prefixo de `billing.py` for diferente) e o nome exato da dependência `get_audit_service` importada em `billing_router` (conferir o `from ... import` no topo de `billing.py` antes de referenciar `billing_router.get_audit_service` no override).

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_billing_recurring_document_fallback.py -v`
Expected: FAIL com 400 "Documento (CPF/CNPJ) é obrigatório para cobrança recorrente." — hoje o router não busca `markets`, então `resolve_billing_document(None, user)` (sem markets) devolve `None` pra um usuário sem CPF.

- [ ] **Step 3: Editar `billing.py`**

Perto da linha 133-140, trocar:
```python
    # recurring
    from application.services.billing_document import resolve_billing_document
    document = resolve_billing_document(dto.document, current_user)
    if not document:
        raise HTTPException(status_code=400, detail="Documento (CPF/CNPJ) é obrigatório para cobrança recorrente.")
    from application.services.recurring_service import RecurringService
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository, SQLAlchemyUserRepository
```
por:
```python
    # recurring
    from application.services.billing_document import resolve_billing_document
    from infra.repositories.sqlalchemy_repos import (
        SQLAlchemyMarketRepository, SQLAlchemyPlanRepository, SQLAlchemyUserRepository,
    )
    markets = await SQLAlchemyMarketRepository(db).list_by_owner(current_user.id)
    document = resolve_billing_document(dto.document, current_user, markets=markets)
    if not document:
        raise HTTPException(status_code=400, detail="Documento (CPF/CNPJ) é obrigatório para cobrança recorrente.")
    from application.services.recurring_service import RecurringService
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
```

(`SQLAlchemyPlanRepository` e `SQLAlchemyUserRepository` saem do import de baixo porque já estão no import combinado de cima — não duplicar o import.)

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_billing_recurring_document_fallback.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/infra/web/routers/billing.py tests/unit/test_billing_recurring_document_fallback.py
git commit -m "feat(billing): fetch owner's markets for the recurring checkout document fallback (D4)"
```

---

### Task 8: `auth.py` router — `/auth/me` cai pro documento da loja também

**Files:**
- Modify: `app/infra/web/routers/auth.py`
- Modify: `tests/unit/test_billing_document.py` (estender `test_auth_me_exposes_only_the_masked_document`)

**Interfaces:**
- Consumes: `registered_document(user, markets)`, `mask_document` (Task 6).

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `tests/unit/test_billing_document.py`:

```python
def test_auth_me_falls_back_to_market_document_when_no_personal_cpf(monkeypatch):
    from datetime import datetime
    from infra.web.main import app
    from infra.web.routers import auth as auth_router

    class StubMarketRepo:
        def __init__(self, db):
            pass

        async def list_by_owner(self, owner_id):
            return [SimpleNamespace(document="12345678000195", created_at=datetime(2023, 1, 1))]

    monkeypatch.setattr("infra.repositories.sqlalchemy_repos.SQLAlchemyMarketRepository", StubMarketRepo)

    app.dependency_overrides[auth_router.get_current_user] = lambda: _user(cpf=None)
    app.dependency_overrides[auth_router.get_db] = lambda: None
    try:
        response = TestClient(app).get("/api/v1/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["document_masked"] == "**.345.678/****-**"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python -m pytest tests/unit/test_billing_document.py -v`
Expected: FAIL — `document_masked` vem `None`, `/me` hoje chama `registered_document(current_user)` sem `markets`.

- [ ] **Step 3: Editar `auth.py`**

Perto da linha 225 — trocar:
```python
        document_masked=mask_document(registered_document(current_user)),
```
por, buscando as lojas do dono antes do `return`:
```python
    from infra.repositories.sqlalchemy_repos import SQLAlchemyMarketRepository
    markets = await SQLAlchemyMarketRepository(db).list_by_owner(current_user.id)

    return UserResponseDTO(
        id=current_user.id,
        name=current_user.name,
        email=email_val,
        role=role_val,
        plan_id=current_user.plan_id,
        plan_name=plan_name,
        plan_expiration=current_user.plan_expiration,
        is_active=current_user.is_active,
        document_masked=mask_document(registered_document(current_user, markets=markets)),
    )
```

(Adicionar o import `SQLAlchemyMarketRepository` perto do topo do arquivo, junto de `SQLAlchemyPlanRepository`, em vez de inline, se o estilo do arquivo já importar os outros repositórios no topo — conferir `auth.py:1-25` antes de decidir.)

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python -m pytest tests/unit/test_billing_document.py -v`
Expected: PASS — inclui o teste antigo `test_auth_me_exposes_only_the_masked_document` (usuário com CPF, sem lojas — `list_by_owner` do stub padrão precisa devolver `[]` nesse teste; conferir se o teste antigo precisa de um `dependency_overrides` extra ou monkeypatch pra não quebrar por causa da nova chamada ao repositório de mercados).

- [ ] **Step 5: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/infra/web/routers/auth.py tests/unit/test_billing_document.py
git commit -m "feat(auth): /auth/me falls back to the market document when there's no personal CPF (D4)"
```

---

## Self-Review

**Cobertura do spec:** §4 (modelo de dados) → Task 2. §5 linha a linha: `identity.py`→Task 2, `validators.py`→Task 1, `dtos.py`→Task 3, `identity_service.py`→Task 4, `sqlalchemy_repos.py`→Task 5, `billing_document.py`→Task 6, `billing.py`→Task 7, `auth.py`→Task 8. §6 (decisão de produto) → resolvida no header do plano (loja mais antiga). §7 (compatibilidade) → coberta pelos testes de "prioriza CPF pessoal" em Tasks 6/8, e pelo fix crítico da Task 2 que é justamente sobre não quebrar contas existentes/novas sem CPF. §9 (testes backend) → todos os 8 bullets têm teste equivalente nas tasks acima. §10/§11 (fora de escopo/riscos) → nenhuma task toca `FiscalTenantConfig` ou o wizard fiscal.

**Placeholder scan:** nenhum "TODO"/"implementar depois" — os dois pontos marcados como "conferir antes de finalizar" (construtor de `IdentityService` na Task 4, prefixo de rota e nome de dependência na Task 7, estilo de import na Task 8) são instruções explícitas de verificação against código real, não lacunas de design — o comportamento esperado já está especificado em cada caso.

**Consistência de tipos:** `registered_document(user, markets=None)` e `resolve_billing_document(provided, user, markets=None)` usam a mesma assinatura de parâmetro (`markets`) nas Tasks 6, 7 e 8. `validate_document` (Task 1) é consumido por `MarketCreateDTO` (Task 3) com o mesmo nome. `Market.document` é `str` de forma consistente da Task 2 em diante — nenhuma task depois disso reintroduz `CNPJ`.
