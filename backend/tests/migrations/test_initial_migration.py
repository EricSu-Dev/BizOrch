from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy import text

from app.persistence.base import Base


EXPECTED_TABLES = {
    "action_executions",
    "action_plan_steps",
    "action_plans",
    "action_proposals",
    "approval_decisions",
    "approvals",
    "auth_sessions",
    "evaluation_bad_cases",
    "evaluation_baselines",
    "evaluation_case_results",
    "evaluation_events",
    "evaluation_runs",
    "idempotency_records",
    "knowledge_chunks",
    "knowledge_document_events",
    "knowledge_documents",
    "knowledge_index_jobs",
    "conversations",
    "conversation_messages",
    "retrieval_logs",
    "service_requests",
    "ticket_events",
    "tickets",
    "tool_calls",
    "user_roles",
    "users",
    "workflow_events",
    "workflow_runs",
}


def alembic_config(database_url: str) -> Config:
    config_path = Path(__file__).parents[2] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_initial_migration_upgrade_downgrade_and_reupgrade(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("BIZORCH_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"
    config = alembic_config(database_url)
    engine = create_engine(database_url)

    command.upgrade(config, "head")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables

    with engine.connect() as connection:
        differences = compare_metadata(
            MigrationContext.configure(connection),
            Base.metadata,
        )
    assert differences == []

    command.downgrade(config, "base")
    downgraded = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.isdisjoint(downgraded)

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_knowledge_governance_migration_backfills_lifecycle_states(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'knowledge-backfill.db'}"
    config = alembic_config(database_url)
    engine = create_engine(database_url)
    command.upgrade(config, "20260719_0006")

    rows = [
        {"id": "indexed", "index_status": "INDEXED"},
        {"id": "failed", "index_status": "FAILED"},
        {"id": "retired", "index_status": "RETIRED"},
    ]
    with engine.begin() as connection:
        for row in rows:
            connection.execute(
                text(
                    "INSERT INTO knowledge_documents ("
                    "id, knowledge_space, title, source_uri, version_label, "
                    "content_digest, source_department, trust_level, "
                    "effective_from, allowed_roles, allowed_user_ids, "
                    "index_status, index_version, chunk_count, created_by"
                    ") VALUES ("
                    ":id, 'test', :id, :source_uri, 'v1', :digest, '部门', "
                    "'HIGH', '2026-07-19 00:00:00', '[]', '[]', "
                    ":index_status, 'index-v1', 1, 'EMP-ADMIN'"
                    ")"
                ),
                {
                    **row,
                    "source_uri": f"policy://{row['id']}",
                    "digest": row["id"].ljust(64, "0"),
                },
            )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        states = {
            row.id: (row.publication_status, row.index_status)
            for row in connection.execute(
                text(
                    "SELECT id, publication_status, index_status "
                    "FROM knowledge_documents"
                )
            )
        }
    assert states == {
        "indexed": ("PUBLISHED", "INDEXED"),
        "failed": ("DRAFT", "FAILED"),
        "retired": ("RETIRED", "INDEXED"),
    }

    command.downgrade(config, "20260719_0006")
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("knowledge_documents")
    }
    assert "publication_status" not in columns
    with engine.connect() as connection:
        downgraded = {
            row.id: row.index_status
            for row in connection.execute(
                text("SELECT id, index_status FROM knowledge_documents")
            )
        }
    assert downgraded == {
        "indexed": "INDEXED",
        "failed": "FAILED",
        "retired": "RETIRED",
    }
