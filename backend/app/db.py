from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .settings import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=8, max_overflow=8,
                       connect_args={"options": "-c timezone=UTC"})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_session():
    with SessionLocal() as session:
        yield session
