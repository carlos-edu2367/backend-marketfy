import asyncio
from logging.config import fileConfig
import os
import sys

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# =============================================================================
# CORREÇÃO DE PATH E IMPORTAÇÕES
# =============================================================================
# Adiciona o diretório 'app' ao sys.path para permitir importações como 'infra.xxx'
current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.join(current_dir, "..", "app")
sys.path.append(app_dir)

# Importa as configurações e o Base do SQLAlchemy
from infra.config.settings import get_settings
from infra.database.setup import Base

# IMPORTANTE: Importar os modelos para que o metadata registre as tabelas
# Sem isso, o autogenerate não detecta nada.
from infra.database.models import * # =============================================================================
# CONFIGURAÇÃO DO ALEMBIC
# =============================================================================

# Objeto de configuração do Alembic
config = context.config

# Configura log
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Define o metadata alvo para as migrações (suas tabelas)
target_metadata = Base.metadata

# Sobrescreve a URL do banco de dados com a do settings.py (pydantic)
# Isso evita ter que colocar credenciais no alembic.ini
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())