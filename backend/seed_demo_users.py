"""CLI for explicitly creating the local demonstration accounts."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from app.auth.seed import seed_demo_users
from app.auth.service import AuthService
from app.core.config import Settings
from app.persistence.database import build_engine, build_session_factory
from start import run_migrations


DEVELOPMENT_DEMO_PASSWORD = "123456"


def resolve_seed_password(
    *, environment: str,
    allow_production_demo_seed: bool,
    configured_password: str | None,
) -> str:
    if environment.lower() != "development":
        if not allow_production_demo_seed:
            raise RuntimeError(
                "demo account seeding outside development requires an explicit opt-in"
            )
        if not configured_password or len(configured_password) < 8:
            raise RuntimeError(
                "BIZORCH_DEMO_PASSWORD must contain at least 8 characters"
            )
    return configured_password or DEVELOPMENT_DEMO_PASSWORD


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed BizOrch demonstration users")
    parser.add_argument("--allow-production-demo-seed", action="store_true")
    parser.add_argument("--reset-existing-credentials", action="store_true")
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parent.parent
    load_dotenv(repository_root / ".env", override=False)
    settings = Settings()
    demo_password = resolve_seed_password(
        environment=settings.environment,
        allow_production_demo_seed=args.allow_production_demo_seed,
        configured_password=os.getenv("BIZORCH_DEMO_PASSWORD"),
    )
    if not settings.database_url.strip():
        raise RuntimeError("BIZORCH_DATABASE_URL must be configured")
    root = Path(__file__).resolve().parent
    run_migrations(settings.database_url, config_path=root / "alembic.ini")
    engine = build_engine(settings.database_url)
    try:
        auth = AuthService(build_session_factory(engine))
        users = seed_demo_users(
            auth,
            employee_password=demo_password,
            manager_password=demo_password,
            operator_password=demo_password,
            hr_password=demo_password,
            reset_existing_credentials=args.reset_existing_credentials,
        )
        print("seeded demo users: " + ", ".join(user.username for user in users))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
