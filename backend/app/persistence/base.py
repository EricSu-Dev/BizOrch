"""Declarative SQLAlchemy base shared by authoritative MySQL models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for BizOrch relational models."""

