# Spec — Documento fiscal (CPF/CNPJ) migra do cadastro para a criação da loja

- **Data:** 2026-09-14
- **Status:** Aguardando revisão do usuário antes do plano de implementação
- **Repos afetados:** `marketfy/backend` (principal), `marketfy/frontend` (seção 8)
- **Decisão de origem:** D4 do plano de conversão original, resolvida em conversa com o usuário em 2026-09-14 — ver seção 3.
- **Não modificar:** `FiscalTenantConfig.cnpj` e o wizard de onboarding fiscal — é um CNPJ diferente, com regime tributário e IE, capturado depois, com validação própria. Fora de escopo.

---

## 1. Contexto e problema

O cadastro (`POST /identity/register`) exige CPF do dono da conta. Um passo depois — obrigatório antes de usar o produto — a criação da primeira loja (`POST /identity/markets`) exige CNPJ. São dois documentos pedidos em telas consecutivas, o primeiro deles logo no momento de maior fricção (antes da pessoa ver qualquer valor do produto).

Verificado no código (2026-09-14):

- `UserCreateDTO.cpf: str` é obrigatório; `UserModel.cpf` no banco já é `nullable=True` — a obrigatoriedade é só da aplicação, não do schema.
- `MarketCreateDTO.document: str` é validado no frontend só por tamanho (`min(14)`, `Dashboard.jsx:16`) e no backend só é aceito como `CNPJ` (`identity_service.create_market` → `Market(document=CNPJ(dto.document), ...)`), sem checagem de dígito verificador em nenhuma das duas pontas.
- `Market.document` (domínio) é tipado como `CNPJ`, usado em 2 pontos no backend (`identity_service.py:85`, `sqlalchemy_repos.py:208,220`) e impresso como `"CNPJ: {market.document}"` no cupom fiscal (`Receipt.jsx:124`, com o rótulo fixo, não condicional).
- `FiscalTenantConfig.cnpj` (usado de fato na emissão de NFC-e, com `tax_regime`, `crt`, `state_registration`) é um campo **separado**, coletado no wizard fiscal (`FiscalOnboardingWizard.jsx`) — não depende de `Market.document`.
- `CPF`/`CNPJ` (value objects em `domain/shared.py`) validam só o tamanho (11/14 dígitos), não o dígito verificador. `validate_cnpj()` (em `domain/validators.py`) faz a validação matemática completa, mas **não é chamada** pelo value object `CNPJ` — é usada separadamente, onde a regra de negócio explicitamente quer validar entrada nova (ex.: `FiscalTenantConfig`). Não existe `validate_cpf()` equivalente.
- `billing_document.registered_document(user)` (adicionado na rodada de conversão anterior) só olha `user.cpf`; não existe hoje fallback para o documento da loja.

## 2. Objetivo

1. Cadastro (`Register.jsx`) pede só nome, e-mail e senha — sem CPF.
2. Criação da loja aceita **CPF ou CNPJ**, com validação de dígito verificador nos dois formatos.
3. `billing_document.registered_document()` cai para o documento da loja mais antiga do dono quando não houver CPF pessoal (contas antigas continuam com o que já têm).
4. Cupom fiscal mostra o rótulo certo ("CPF:" ou "CNPJ:") conforme o documento realmente armazenado.

## 3. Decisão de produto (fechada com o usuário)

> Mover a coleta do documento fiscal: cadastro inicial pede só nome/e-mail/senha. CNPJ (ou CPF pra quem for MEI/informal sem CNPJ) é pedido na criação da primeira loja, um passo depois — reduz fricção no topo do funil, que é onde mais gente desiste.

Confirmado também: `Market.document` não é usado na emissão fiscal (isso é `FiscalTenantConfig.cnpj`, separado) — então flexibilizar seu formato não tem risco fiscal/legal na emissão de NFC-e em si. Continua valendo a regra de negócio: para emitir NFC-e de verdade, a loja precisa passar pelo wizard fiscal e preencher lá um CNPJ próprio, com regime tributário — isso não muda.

## 4. Modelo de dados

**Nenhuma migration é necessária.** As duas colunas relevantes já suportam o que se precisa:

- `UserModel.cpf` já é `nullable=True` no banco — só a validação da aplicação exigia o campo.
- `MarketModel.document` já é `String` genérico (`nullable=False, unique=True`) — não há coluna tipada para CNPJ especificamente; a tipagem `CNPJ` só existe na camada de domínio Python.

Mudanças de domínio (Python, sem schema):

- `domain/identity.py`: `User.cpf: CPF` → `User.cpf: Optional[CPF] = None`.
- `domain/identity.py`: `Market.document: CNPJ` → `Market.document: str` (dígitos, sem tentar tipar como CNPJ especificamente — CPF e CNPJ têm formatos de exibição diferentes; guardar como string simples e formatar na borda evita forçar um dos dois tipos).
- `domain/validators.py`: nova função `validate_cpf(cpf: str) -> bool`, mesmo algoritmo de dígito verificador que `validate_cnpj`, adaptado para 11 dígitos (pesos `[10,9,8,7,6,5,4,3,2]` pro primeiro dígito, `[11,10,9,8,7,6,5,4,3,2]` pro segundo — algoritmo padrão de CPF).
- Nova função `validate_document(digits: str) -> bool` em `validators.py`: despacha para `validate_cpf` (11 dígitos) ou `validate_cnpj` (14 dígitos); retorna `False` para qualquer outro tamanho.

## 5. Mudanças por arquivo (backend)

| Arquivo | Mudança |
|---|---|
| `app/domain/identity.py` | `User.cpf` vira `Optional[CPF]`; `Market.document` vira `str` |
| `app/domain/validators.py` | `validate_cpf()`, `validate_document()` |
| `app/application/dtos.py` | `UserCreateDTO.cpf` removido (ou vira `Optional[str] = None`, ver §7); `MarketCreateDTO.document` ganha `field_validator` chamando `validate_document` |
| `app/application/services/identity_service.py` | `register_user`: não passa mais `cpf=CPF(dto.cpf)` incondicionalmente — só se vier preenchido. `create_market`: `document=re.sub(r'\D', '', dto.document)` em vez de `CNPJ(dto.document)` |
| `app/infra/repositories/sqlalchemy_repos.py` | `save`: `model.document = market.document` (sem `.value`, já é string); `_to_entity`: `document=m.document` (sem `CNPJ(...)`) |
| `app/application/services/billing_document.py` | `registered_document()` ganha parâmetro `markets: Optional[list]`; se `user.cpf` ausente, usa o documento da loja mais antiga (`min(markets, key=lambda m: m.created_at)`) |
| `app/infra/web/routers/billing.py` | No ramo `recurring`, busca `SQLAlchemyMarketRepository(db).list_by_owner(current_user.id)` e passa pro `resolve_billing_document` |
| `app/infra/web/routers/auth.py` | `document_masked` em `/auth/me` também cai pro documento da loja quando não há CPF pessoal (mesma função, então herda automaticamente) |

## 6. Decisão em aberto — não resolvida por mim, fica documentada

**Dono com mais de uma loja com documentos diferentes:** hoje o sistema já permite múltiplas lojas por dono (`max_markets` por plano). Se o CPF pessoal não existir e houver 2+ lojas com documentos diferentes, qual usar como padrão no checkout recorrente?

Proposta (não decidida): usar a loja mais antiga (`created_at` mínimo), com a opção de digitar outro documento no checkout continuando disponível (já existe — ver `Plans.jsx`, checkbox "usar o CPF do cadastro"). Isso é uma decisão de produto, não técnica — o usuário deve confirmar ou propor outra regra antes da implementação.

## 7. Compatibilidade com contas existentes

- Usuários que já têm `User.cpf` preenchido continuam exatamente como estão — `registered_document()` prioriza `user.cpf` quando presente, só cai para o documento da loja na ausência dele.
- `UserCreateDTO.cpf` vira **opcional** (não obrigatório) em vez de removido — mantém a rota `/register` aceitando o campo se algum cliente antigo do front (ex.: app mobile futuro) ainda enviar, sem quebrar; o formulário web simplesmente para de coletá-lo.
- Nenhum dado existente é migrado ou reescrito.

## 8. Frontend (`marketfy-frontend`)

- **`Register.jsx`:** remove o campo CPF, `maskCpf`/`onlyDigits` import (se não sobrar outro uso), schema zod, e a chamada correspondente em `onSubmit`.
- **`Dashboard.jsx`** (formulário de criar loja): campo único "CPF ou CNPJ", schema zod `z.string().refine(v => onlyDigits(v).length === 11 || onlyDigits(v).length === 14, 'CPF ou CNPJ inválido')`; nova função `maskDocument(value)` em `lib/documentMask.js` que aplica `maskCpf` até 11 dígitos digitados e passa a aplicar máscara de CNPJ a partir do 12º dígito (reaproveita o padrão de detecção incremental já usado em `maskCpf`).
- **`lib/documentMask.js`:** nova `maskCnpj(value)` (formato `00.000.000/0000-00`) e `maskDocument(value)` que escolhe entre as duas pelo tamanho.
- **`components/pdv/Receipt.jsx:124`:** trocar `` `CNPJ: ${marketInfo.document}` `` por um rótulo condicional (`onlyDigits(marketInfo.document).length === 11 ? 'CPF' : 'CNPJ'`).

## 9. Testes a cobrir

**Backend:**
- `validate_cpf`: CPFs válidos conhecidos passam; todos-dígitos-iguais falha; dígito verificador errado falha (mesmo padrão de `test_tax_rule_calculator.py`-style de `validate_cnpj`, se existir um teste espelho — senão, seguir o estilo dos testes unitários já usados para `validate_cnpj`).
- `validate_document`: 11 dígitos válidos → True; 14 dígitos válidos → True; 12 dígitos → False; 11 dígitos com checksum errado → False.
- `register_user` sem `cpf` no payload → sucesso, `User.cpf is None`.
- `create_market` com CPF de 11 dígitos válido → sucesso, `Market.document` guarda os 11 dígitos.
- `create_market` com CNPJ válido → sucesso (regressão do comportamento atual).
- `create_market` com documento de tamanho/checksum inválido → erro 400.
- `billing_document.registered_document(user_sem_cpf, markets=[loja_mais_nova, loja_mais_antiga])` → retorna o documento da loja mais antiga.
- `billing_document.registered_document(user_com_cpf, markets=[...])` → CPF pessoal continua vencendo (nenhuma regressão).

**Frontend:**
- `Register.jsx`: formulário sem campo CPF; submit não envia `cpf`.
- `Dashboard.jsx` (form de loja): digitar 11 dígitos aplica máscara de CPF; digitar 14 aplica máscara de CNPJ; ambos válidos submetem; inválido mostra erro.
- `Receipt.jsx`: rótulo "CPF:" quando o documento tem 11 dígitos, "CNPJ:" quando tem 14.

## 10. Fora de escopo

- `FiscalTenantConfig.cnpj` e o wizard fiscal — já têm validação e fluxo próprios.
- CNPJ no cadastro pessoal (`User`) — continua não existindo; o domínio já tinha `User.cnpj: Optional[CNPJ]` declarado mas nunca persistido (`UserModel` não tem essa coluna) — permanece assim, não faz parte desta mudança.
- Migração de dados de contas existentes.

## 11. Riscos

- Baixo: mudança de tipo em `Market.document` (`CNPJ` → `str`) tem blast radius pequeno e mapeado (§5) — só 2 arquivos de backend e 2 de frontend tocam o campo diretamente, confirmado por busca no código nas duas bases. Busca por `Market(document=CNPJ(` em `tests/` não encontrou nenhum teste construindo `Market` dessa forma diretamente — nenhum ajuste de teste por tipagem esperado além dos listados em §9.
