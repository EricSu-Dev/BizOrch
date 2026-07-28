"""Versioned schema upgrade entrypoint for the simulated enterprise system."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade_enterprise_schema(database_url: str) -> None:
    """Apply all enterprise migrations before accepting service traffic."""
    if not database_url.strip():
        raise ValueError("enterprise database_url must be configured")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    # Alembic Config uses ConfigParser interpolation; URL-encoded credentials
    # can legitimately contain percent signs and must not be interpreted as
    # configuration placeholders.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
