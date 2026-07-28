from pathlib import Path

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.models import User
from app.auth.passwords import PasswordHasher
from app.auth.seed import seed_demo_users
from app.auth.service import AuthService
from start import run_migrations, wait_for_database


def config_path() -> Path:
    return Path(__file__).parents[2] / "alembic.ini"


def test_container_bootstrap_waits_and_runs_migration(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BIZORCH_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'startup.db'}"

    wait_for_database(database_url, attempts=1, delay_seconds=0)
    run_migrations(database_url, config_path=config_path())

    tables = set(inspect(create_engine(database_url)).get_table_names())
    assert {"users", "workflow_runs", "approvals"} <= tables


def test_demo_user_seed_is_idempotent_and_uses_supplied_passwords(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("BIZORCH_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'seed.db'}"
    run_migrations(database_url, config_path=config_path())
    engine = create_engine(database_url)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    auth = AuthService(sessions, password_hasher=PasswordHasher(n=2**10))

    first = seed_demo_users(
        auth,
        employee_password="employee-password",
        manager_password="manager-password",
        operator_password="operator-password",
        hr_password="hr-password",
    )
    second = seed_demo_users(
        auth,
        employee_password="unused-new-password",
        manager_password="unused-new-password",
        operator_password="unused-new-password",
        hr_password="unused-new-password",
    )

    assert first == second
    with sessions() as session:
        assert len(session.scalars(select(User)).all()) == 6
    assert auth.login(
        username="employee", password="employee-password"
    ).user.employee_id == "EMP-1001"
    assert auth.login(
        username="manager", password="manager-password"
    ).user.employee_id == "EMP-MANAGER"
    operator = auth.login(username="operator", password="operator-password").user
    assert operator.employee_id == "EMP-KNOWLEDGE-OPERATOR"
    assert {role.value for role in operator.roles} == {"operator"}
    hr = auth.login(username="hr", password="hr-password").user
    assert hr.employee_id == "EMP-HR-OPERATOR"
    assert {role.value for role in hr.roles} == {"hr"}
    budget_owner = auth.login(
        username="budget.owner",
        password="manager-password",
    ).user
    assert budget_owner.employee_id == "EMP-BUDGET-OWNER"
    assert {role.value for role in budget_owner.roles} == {"approver"}
    procurement_owner = auth.login(
        username="procurement.owner",
        password="manager-password",
    ).user
    assert procurement_owner.employee_id == "EMP-PROCUREMENT-OWNER"
    assert {role.value for role in procurement_owner.roles} == {"approver"}
