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
