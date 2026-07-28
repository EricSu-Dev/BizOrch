from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from enterprise_system.app.persistence import EnterpriseBase
from enterprise_system.app.schema import upgrade_enterprise_schema


EXPECTED_TABLES = {
    "enterprise_access_requests",
    "enterprise_applications",
    "enterprise_employees",
    "enterprise_equipment",
    "enterprise_idempotency_records",
    "enterprise_maintenance_history",
    "enterprise_maintenance_work_orders",
    "enterprise_user_access",
    "enterprise_organization_units",
    "enterprise_job_profiles",
    "enterprise_corporate_accounts",
    "enterprise_access_packages",
    "enterprise_asset_tasks",
    "enterprise_employee_lifecycle_requests",
    "enterprise_work_locations",
    "enterprise_cost_centers",
    "enterprise_procurement_policies",
    "enterprise_procurement_requests",
    "enterprise_procurement_request_items",
    "enterprise_budget_reservations",
}


def alembic_config(database_url: str) -> Config:
    path = Path(__file__).parents[1] / "alembic.ini"
    config = Config(str(path))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_enterprise_migrations_upgrade_match_metadata_and_downgrade(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ENTERPRISE_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'enterprise-migration.db'}"
    config = alembic_config(database_url)
    engine = create_engine(database_url)

    command.upgrade(config, "head")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables

    with engine.connect() as connection:
        differences = compare_metadata(
            MigrationContext.configure(connection),
            EnterpriseBase.metadata,
        )
    assert differences == []

    command.downgrade(config, "base")
    assert EXPECTED_TABLES.isdisjoint(inspect(engine).get_table_names())

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_employee_status_is_backfilled_from_legacy_active_flag(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ENTERPRISE_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'enterprise-backfill.db'}"
    config = alembic_config(database_url)
    engine = create_engine(database_url)
    command.upgrade(config, "20260719_0003")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO enterprise_employees "
            "(employee_id, display_name, department_code, manager_id, active) "
            "VALUES ('EMP-OLD-ACTIVE', 'Active', 'OLD', NULL, 1), "
            "('EMP-OLD-INACTIVE', 'Inactive', 'OLD', NULL, 0), "
            "('EMP-2001', 'Demo', 'PLANT-WORKSHOP-1', NULL, 1)"
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT employee_id, employment_status, job_code, "
            "work_location_code, version FROM enterprise_employees "
            "ORDER BY employee_id"
        ).all()
    assert rows == [
        ("EMP-2001", "ACTIVE", "PLANT-OPERATOR", "PLANT-EAST", 1),
        ("EMP-OLD-ACTIVE", "ACTIVE", "UNASSIGNED", "UNKNOWN", 1),
        ("EMP-OLD-INACTIVE", "INACTIVE", "UNASSIGNED", "UNKNOWN", 1),
    ]


def test_runtime_upgrade_accepts_percent_in_database_url(tmp_path, monkeypatch) -> None:
    """URL-encoded production credentials must not trigger ConfigParser interpolation."""
    monkeypatch.delenv("ENTERPRISE_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'enterprise%runtime.db'}"

    upgrade_enterprise_schema(database_url)

    tables = set(inspect(create_engine(database_url)).get_table_names())
    assert EXPECTED_TABLES <= tables
