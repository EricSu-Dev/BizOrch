"""CLI for explicitly creating the local demonstration accounts."""

import os
from pathlib import Path

from dotenv import load_dotenv

from app.auth.seed import seed_demo_users
from app.auth.service import AuthService
from app.core.config import Settings
from app.persistence.database import build_engine, build_session_factory
from start import run_migrations


def _required_secret(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def main() -> None:
    repository_root = Path(__file__).resolve().parent.parent
    load_dotenv(repository_root / ".env", override=False)
    settings = Settings()
    if not settings.database_url.strip():
        raise RuntimeError("BIZORCH_DATABASE_URL must be configured")
    employee_password = _required_secret("BIZORCH_DEMO_EMPLOYEE_PASSWORD")
    manager_password = _required_secret("BIZORCH_DEMO_MANAGER_PASSWORD")
    operator_password = _required_secret("BIZORCH_DEMO_OPERATOR_PASSWORD")
    hr_password = _required_secret("BIZORCH_DEMO_HR_PASSWORD")
    root = Path(__file__).resolve().parent
    run_migrations(settings.database_url, config_path=root / "alembic.ini")
    engine = build_engine(settings.database_url)
    try:
        auth = AuthService(build_session_factory(engine))
        users = seed_demo_users(
            auth,
            employee_password=employee_password,
            manager_password=manager_password,
            operator_password=operator_password,
            hr_password=hr_password,
        )
        print("seeded demo users: " + ", ".join(user.username for user in users))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
