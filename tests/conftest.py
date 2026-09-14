"""Configuração global de testes.

Define variáveis de ambiente mínimas ANTES de qualquer import de módulos da
aplicação, pois `infra.database.setup` e `infra.config.settings` são avaliados
no import e exigem `DATABASE_URL`/`SECRET_KEY`. A URL do banco nunca é conectada
nos testes unitários (o engine é criado de forma lazy; testes que precisam de DB
usam SQLite em memória com engine próprio).
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/marketfy_test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-com-mais-de-32-caracteres-ok")
os.environ.setdefault("FISCAL_SECRET_KEY", "test-fiscal-secret-key-com-mais-de-32-chars-ok")
os.environ.setdefault("ENVIRONMENT", "development")

# Modelos usam sqlalchemy.dialects.postgresql.UUID (Entity.id, FKs). Esse tipo
# não sabe compilar DDL para SQLite, então qualquer teste que rode
# Base.metadata.create_all contra um engine sqlite+aiosqlite quebra na criação
# das tabelas. O bind/result processor do tipo já converte uuid.UUID <-> str
# independente do dialeto; só falta um alvo de compilação para SQLite. Este
# shim ensina isso e vale só nos testes (nunca roda contra o Postgres real).
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.dialects.postgresql import UUID as _PostgresUUID  # noqa: E402


@compiles(_PostgresUUID, "sqlite")
def _compile_postgres_uuid_as_char_for_sqlite(element, compiler, **kw):
    return "CHAR(32)"
