"""Private persistence owned by the independent simulated enterprise service."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class EnterpriseBase(DeclarativeBase):
    """Separate metadata prevents domain tables entering BizOrch core."""


def build_enterprise_engine(database_url: str) -> Engine:
    if not database_url.strip():
        raise ValueError("enterprise database_url must be configured")
    return create_engine(database_url, pool_pre_ping=True)


def build_enterprise_session_factory(
    engine: Engine,
) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)

