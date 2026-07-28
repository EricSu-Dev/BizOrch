"""Persistence contracts for V6 evaluation governance records."""

from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.evaluation.contracts import (
    EvaluationBadCaseSeverity,
    EvaluationBadCaseStatus,
    EvaluationCaseStatus,
    EvaluationRunMode,
    EvaluationRunStatus,
)
from app.evaluation.models import (
    EvaluationBadCase,
    EvaluationBaseline,
    EvaluationCaseResultRecord,
    EvaluationEvent,
    EvaluationRun,
)
from app.persistence.base import Base


def _run(run_id: str) -> EvaluationRun:
    return EvaluationRun(
        id=run_id,
        suite_key="v2_agent_rag",
        suite_name="Fixed suite",
        suite_version="2026.2",
        content_digest="sha256:" + "a" * 64,
        selected_case_ids=["case_one"],
        mode=EvaluationRunMode.CONTRACT_ONLY.value,
        status=EvaluationRunStatus.PENDING.value,
        created_by="EMP-OPERATOR",
        command_key=f"command-{run_id}",
        total_case_count=1,
        evaluation_rules_version="2026.1",
        safe_configuration_snapshot={"model": "not-called"},
    )


def test_evaluation_records_preserve_safe_references_without_prompt_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'evaluation.db'}")
    Base.metadata.create_all(engine)

    run = _run("run-1")
    result = EvaluationCaseResultRecord(
        id="result-1",
        run=run,
        case_id="case_one",
        category="SAFETY_SCHEMA",
        status=EvaluationCaseStatus.FAILED.value,
        failure_code="ASSERTION_FAILED",
        safe_failure_summary="字段断言未通过",
        result_summary="结构化断言失败",
    )
    bad_case = EvaluationBadCase(
        id="bad-case-1",
        source_case_result=result,
        source_run_id="run-1",
        source_case_id="case_one",
        suite_key="v2_agent_rag",
        suite_version="2026.2",
        content_digest="sha256:" + "a" * 64,
        case_id="case_one",
        category="SAFETY_SCHEMA",
        status=EvaluationBadCaseStatus.OPEN.value,
        severity=EvaluationBadCaseSeverity.HIGH.value,
        safe_issue_summary="固定安全断言失败",
        created_by="EMP-OPERATOR",
    )
    event = EvaluationEvent(
        id="event-1",
        run=run,
        bad_case=bad_case,
        event_type="BAD_CASE_CREATED",
        actor_id="EMP-OPERATOR",
        actor_roles=["operator"],
        safe_payload={"case_id": "case_one"},
    )

    with Session(engine) as session:
        session.add_all([run, result, bad_case, event])
        session.commit()
        persisted = session.get(EvaluationBadCase, "bad-case-1")
        assert persisted is not None
        assert persisted.source_case_result.safe_failure_summary == "字段断言未通过"
        assert persisted.events[0].safe_payload == {"case_id": "case_one"}

    columns = {
        table: {column["name"] for column in inspect(engine).get_columns(table)}
        for table in {
            "evaluation_runs",
            "evaluation_case_results",
            "evaluation_baselines",
            "evaluation_bad_cases",
            "evaluation_events",
        }
    }
    forbidden = {"prompt", "message", "raw_response", "api_key", "access_token"}
    assert all(forbidden.isdisjoint(table_columns) for table_columns in columns.values())


def test_only_one_active_baseline_key_can_exist_while_history_is_retained(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'baseline.db'}")
    Base.metadata.create_all(engine)
    active_key = "v2_agent_rag:2026.2:sha256:" + "a" * 64 + ":CONTRACT_ONLY"

    with Session(engine) as session:
        session.add_all([_run("run-1"), _run("run-2"), _run("run-3")])
        session.add_all(
            [
                EvaluationBaseline(
                    id="baseline-1",
                    suite_key="v2_agent_rag",
                    suite_version="2026.2",
                    content_digest="sha256:" + "a" * 64,
                    mode=EvaluationRunMode.CONTRACT_ONLY.value,
                    run_id="run-1",
                    set_by="EMP-OPERATOR",
                    is_current=True,
                    active_key=active_key,
                ),
                EvaluationBaseline(
                    id="baseline-history",
                    suite_key="v2_agent_rag",
                    suite_version="2026.2",
                    content_digest="sha256:" + "a" * 64,
                    mode=EvaluationRunMode.CONTRACT_ONLY.value,
                    run_id="run-2",
                    set_by="EMP-OPERATOR",
                    is_current=False,
                    active_key=None,
                ),
            ]
        )
        session.commit()

        session.add(
            EvaluationBaseline(
                id="baseline-duplicate",
                suite_key="v2_agent_rag",
                suite_version="2026.2",
                content_digest="sha256:" + "a" * 64,
                mode=EvaluationRunMode.CONTRACT_ONLY.value,
                run_id="run-3",
                set_by="EMP-OPERATOR",
                is_current=True,
                active_key=active_key,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("duplicate active evaluation baseline key was accepted")


def test_evaluation_tables_compile_for_mysql_8() -> None:
    dialect = mysql.dialect()

    ddl = "\n".join(
        str(CreateTable(model.__table__).compile(dialect=dialect))
        for model in (
            EvaluationRun,
            EvaluationCaseResultRecord,
            EvaluationBaseline,
            EvaluationBadCase,
            EvaluationEvent,
        )
    )

    assert "evaluation_runs" in ddl
    assert "evaluation_case_results" in ddl
    assert "evaluation_bad_cases" in ddl
