"""V6-08 failure-injection evidence that evaluation cannot reach business writes."""

from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.actions.models  # noqa: F401
import app.approval.models  # noqa: F401
import app.conversations.models  # noqa: F401
import app.tickets.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.contracts import EvaluationRunSelection
from app.evaluation.models import EvaluationRun
from app.evaluation.service import EvaluationService
from app.evaluation.worker import EvaluationWorker
from app.persistence.base import Base


ROOT = Path(__file__).resolve().parents[3]
BUSINESS_TABLES = (
    "conversations",
    "conversation_messages",
    "tool_calls",
    "workflow_runs",
    "workflow_events",
    "service_requests",
    "tickets",
    "ticket_events",
    "approval_sequences",
    "approvals",
    "approval_decisions",
    "action_proposals",
    "action_plans",
    "action_plan_steps",
    "action_executions",
    "idempotency_records",
)


def test_contract_evaluation_writes_only_evaluation_tables(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'safety-e2e.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    catalog = EvaluationSuiteCatalog(ROOT)
    run_id = EvaluationService(sessions, catalog).create_run(
        selection=EvaluationRunSelection(suite_key="v2_agent_rag"),
        actor_id="EMP-KNOWLEDGE-OPERATOR",
        command_key=str(uuid4()),
    ).run_id

    EvaluationWorker(
        sessions,
        catalog,
        worker_id="safety-e2e-worker",
    ).run_once()

    with engine.connect() as connection:
        counts = {
            table: connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
            for table in BUSINESS_TABLES
        }
    with sessions() as session:
        run = session.get(EvaluationRun, run_id)
    assert run is not None and run.status == "COMPLETED"
    assert all(count == 0 for count in counts.values())


def test_evaluation_package_has_no_business_command_imports() -> None:
    evaluation_root = ROOT / "backend" / "app" / "evaluation"
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in evaluation_root.glob("*.py")
    )
    forbidden = (
        "app.actions",
        "app.approval",
        "app.workflow",
        ".commands import",
        "build_mcp_enterprise_clients",
        "enterprise_ops_mcp",
    )
    assert all(name not in combined for name in forbidden)
