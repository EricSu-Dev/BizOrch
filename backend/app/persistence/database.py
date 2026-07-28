"""Database engine and session factory helpers."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Build a synchronous SQLAlchemy engine with stale-connection checks."""
    if not database_url.strip():
        raise ValueError("database_url must be configured")
    return create_engine(database_url, echo=echo, pool_pre_ping=True)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions that keep loaded values available after commit."""
    return sessionmaker(bind=engine, expire_on_commit=False)

