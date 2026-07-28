"""V6-06 bounded read-only execution tests with no real provider calls."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.contracts import SupervisorPlan
from app.agents.llm import DeepSeekJsonModel
from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.contracts import (
    EvaluationCaseStatus,
    EvaluationRunMode,
    EvaluationRunSelection,
    EvaluationRunStatus,
)
from app.evaluation.live import LiveReadOnlyCapabilities, LiveReadOnlyCaseExecutor
from app.evaluation.models import EvaluationCaseResultRecord, EvaluationRun
from app.evaluation.service import EvaluationService
from app.evaluation.usage import (
    EvaluationLimitExceededError,
    EvaluationUsageGuard,
    EvaluationUsageLimits,
)
from app.evaluation.worker import EvaluationWorker
from app.knowledge.embeddings import DashScopeEmbeddings
from app.persistence.base import Base


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_usage_guard_reserves_before_io_and_enforces_server_limits() -> None:
    guard = EvaluationUsageGuard(
        EvaluationUsageLimits(
            max_calls=1,
            max_input_tokens=10,
            max_output_tokens=5,
            max_embedding_texts=1,
        )
    )

    guard.before_model_call()
    guard.after_model_call(input_tokens=10, output_tokens=5)

    assert guard.snapshot().call_count == 1
    with pytest.raises(EvaluationLimitExceededError, match="call limit"):
        guard.before_model_call()


def test_provider_adapters_report_actual_returned_usage() -> None:
    guard = EvaluationUsageGuard()
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"intent":"UNKNOWN","scenario_key":null,'
                        '"knowledge_space":null,"rewritten_query":null,'
                        '"scenario_payload":{}}'
                    )
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
    )

    class Completions:
        def create(self, **_kwargs):
            return completion

    model = DeepSeekJsonModel(
        "unused",
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        usage_observer=guard,
    )
    model.generate(SupervisorPlan, system_prompt="classify", user_prompt="hello")

    embedding_response = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.0] * 1024)],
        usage=SimpleNamespace(total_tokens=7),
    )

    class Embeddings:
        def create(self, **_kwargs):
            return embedding_response

    DashScopeEmbeddings(
        "unused",
        client=SimpleNamespace(embeddings=Embeddings()),
        usage_observer=guard,
    ).embed_query("policy")

    snapshot = guard.snapshot()
    assert snapshot.call_count == 2
    assert snapshot.input_token_count == 19
    assert snapshot.output_token_count == 3
    assert snapshot.embedding_text_count == 1


def test_live_worker_persists_usage_and_has_only_read_capability_ports(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'live-worker.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    catalog = EvaluationSuiteCatalog(REPOSITORY_ROOT)
    case = catalog.select_cases(
        "v2_agent_rag",
        ("plan_access_crm_complete",),
    ).cases[0]
    guard = EvaluationUsageGuard()

    class MeteredPlanner:
        def plan(self, message: str) -> SupervisorPlan:
            guard.before_model_call()
            guard.after_model_call(input_tokens=20, output_tokens=5)
            assert message == case.message
            return SupervisorPlan(
                intent=case.expected_intent,
                scenario_key=case.expected_scenario_key,
                knowledge_space=case.expected_knowledge_space,
                scenario_payload=case.expected_payload,
            )

    class UnusedKnowledge:
        def search(self, **_kwargs):
            raise AssertionError("knowledge capability was not selected")

    run_id = EvaluationService(sessions, catalog).create_run(
        selection=EvaluationRunSelection(
            suite_key="v2_agent_rag",
            mode=EvaluationRunMode.LIVE_READ_ONLY,
            case_ids=(case.case_id,),
            confirm_live_external_calls=True,
        ),
        actor_id="EMP-KNOWLEDGE-OPERATOR",
        command_key=str(uuid4()),
    ).run_id
    worker = EvaluationWorker(
        sessions,
        catalog,
        worker_id="live-read-only-worker",
        executor=LiveReadOnlyCaseExecutor(
            LiveReadOnlyCapabilities(
                planner=MeteredPlanner(),
                knowledge=UnusedKnowledge(),
            ),
            guard,
        ),
        allowed_modes=(EvaluationRunMode.LIVE_READ_ONLY,),
    )

    assert worker.run_once() == run_id

    with sessions() as session:
        run = session.get(EvaluationRun, run_id)
        result = session.scalar(
            select(EvaluationCaseResultRecord).where(
                EvaluationCaseResultRecord.run_id == run_id
            )
        )
    assert run is not None and result is not None
    assert run.status == EvaluationRunStatus.COMPLETED.value
    assert result.status == EvaluationCaseStatus.PASSED.value
    assert run.call_count == result.call_count == 1
    assert run.input_token_count == result.input_token_count == 20
    assert run.output_token_count == result.output_token_count == 5


def test_live_limit_error_is_sanitized_and_stops_before_second_call() -> None:
    catalog = EvaluationSuiteCatalog(REPOSITORY_ROOT)
    case = catalog.select_cases(
        "v2_agent_rag",
        ("plan_access_crm_complete",),
    ).cases[0]
    guard = EvaluationUsageGuard(
        EvaluationUsageLimits(
            max_calls=1,
            max_input_tokens=100,
            max_output_tokens=100,
            max_embedding_texts=10,
            max_run_duration=timedelta(minutes=1),
        )
    )
    guard.before_model_call()

    class LimitedPlanner:
        def plan(self, message: str):
            guard.before_model_call()
            raise AssertionError("unreachable")

    class UnusedKnowledge:
        def search(self, **_kwargs):
            raise AssertionError("unreachable")

    outcome = LiveReadOnlyCaseExecutor(
        LiveReadOnlyCapabilities(
            planner=LimitedPlanner(),
            knowledge=UnusedKnowledge(),
        ),
        guard,
    ).execute(case)

    assert outcome.status is EvaluationCaseStatus.ERROR
    assert outcome.failure_code == "EVALUATION_LIMIT_EXCEEDED"
    assert case.message not in (outcome.safe_failure_summary or "")


def test_evaluation_composition_has_no_business_write_imports() -> None:
    source = (
        REPOSITORY_ROOT
        / "backend"
        / "app"
        / "evaluation"
        / "composition.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "app.scenarios",
        "app.actions",
        "app.approval",
        "app.workflow",
        "enterprise_ops_mcp",
        "build_mcp_enterprise_clients",
    )
    assert all(name not in source for name in forbidden)


def test_run_deadline_survives_recovery_and_stops_before_provider_call() -> None:
    ticks = iter((0.0, 61.0))
    guard = EvaluationUsageGuard(
        EvaluationUsageLimits(
            max_calls=2,
            max_input_tokens=100,
            max_output_tokens=100,
            max_embedding_texts=10,
            max_run_duration=timedelta(seconds=60),
        ),
        monotonic_clock=lambda: next(ticks),
    )

    with pytest.raises(EvaluationLimitExceededError, match="duration"):
        guard.check_deadline()
    assert guard.snapshot().call_count == 0
