"""Container bootstrap: wait for MySQL, migrate, then replace with Uvicorn."""

import os
from pathlib import Path
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.config import Settings
from app.persistence.database import build_engine


def wait_for_database(
    database_url: str,
    *,
    attempts: int = 20,
    delay_seconds: float = 3,
) -> None:
    """Wait a bounded time for the configured authoritative database."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        engine = build_engine(database_url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
        finally:
            engine.dispose()
    raise RuntimeError("database did not become ready before timeout") from last_error


def run_migrations(database_url: str, *, config_path: Path) -> None:
    """Upgrade the authoritative schema to the repository head revision."""
    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def main() -> None:
    settings = Settings()
    if not settings.database_url.strip():
        raise RuntimeError("BIZORCH_DATABASE_URL must be configured")
    root = Path(__file__).resolve().parent
    wait_for_database(settings.database_url)
    run_migrations(settings.database_url, config_path=root / "alembic.ini")
    os.execvp(
        "uvicorn",
        (
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--workers",
            "1",
        ),
    )


if __name__ == "__main__":
    main()
