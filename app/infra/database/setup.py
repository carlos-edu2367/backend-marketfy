from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from infra.config.settings import get_settings

settings = get_settings()
DATABASE_URL = settings.DATABASE_URL.strip().replace(
    "postgresql://",
    "postgresql+asyncpg://"
)


from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,   # MUITO IMPORTANTE com PgBouncer
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "command_timeout": 60,
    },
)

AsyncSessionLocal = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

Base = declarative_base()

# Dependency para FastAPI
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# Compatibilidade para o worker do ARQ e os jobs assíncronos
async_session_factory = AsyncSessionLocal


async def init_db():
    """No-op para compatibilidade de inicialização no worker."""
    pass