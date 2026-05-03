from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import DBURL
from db.db_model import Base
from sqlalchemy import text

engine = create_async_engine(DBURL, echo=False, pool_pre_ping=True) # Short-variant of settings!
async_session = async_sessionmaker(
    engine, 
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("SET statement_timeout = 30000"))
        await connection.run_sync(Base.metadata.create_all)