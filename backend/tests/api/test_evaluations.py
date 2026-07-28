"""V6-04 operator-gated evaluation API contracts without a worker or model calls."""

from fastapi.testclient import TestClient
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import CurrentActor, get_current_actor
from app.core.config import Settings
from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.models import EvaluationCaseResultRecord, EvaluationRun
from app.main import create_app
from app.persistence.base import Base


class EvaluationRuntimeHarness:
    def __init__(self, engine) -> None:
        self.engine = engine

    def close(self) -> None:
        self.engine.dispose()


def build_app(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'evaluation-api.db'}")
    Base.metadata.create_all(engine)
    application = create_app(
        settings=Settings(database_url="configured-for-test"),
        runtime_factory=lambda settings: EvaluationRuntimeHarness(engine),
    )
    application.state.test_engine = engine
    return application


def actor(role: str) -> CurrentActor:
    return CurrentActor(user_id=f"EMP-{role.upper()}", roles=frozenset({role}))


def test_operator_can_list_catalog_and_create_idempotent_pending_run(tmp_path) -> None:
    application = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: actor("operator")
    body = {"suite_key": "v2_agent_rag", "case_ids": ["plan_access_crm_complete"]}
    headers = {"Idempotency-Key": "evaluation-create-001"}

    with TestClient(application) as client:
        suites = client.get("/api/v1/evaluations/suites")
        first = client.post("/api/v1/evaluations/runs", json=body, headers=headers)
        replay = client.post("/api/v1/evaluations/runs", json=body, headers=headers)
        listed = client.get("/api/v1/evaluations/runs")

    assert suites.status_code == 200
    assert suites.json()["total"] == len(suites.json()["items"])
    assert {item["suite_key"] for item in suites.json()["items"]} >= {
        "v2_agent_rag",
        "v61_procurement_challenge",
    }
    assert first.status_code == replay.status_code == 201
    assert first.json()["run_id"] == replay.json()["run_id"]
    assert first.json()["status"] == "PENDING"
    assert first.json()["completed_case_count"] == 0
    assert listed.json()["total"] == 1
    assert "selected_case_ids" not in first.json()


def test_non_operator_is_rejected_before_evaluation_services_are_used(tmp_path) -> None:
    application = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: actor("employee")

    with TestClient(application) as client:
        response = client.get("/api/v1/evaluations/suites")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACTION_FORBIDDEN"


def test_live_run_requires_explicit_external_call_confirmation(tmp_path) -> None:
    application = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: actor("admin")

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/evaluations/runs",
            json={"suite_key": "v2_agent_rag", "mode": "LIVE_READ_ONLY"},
            headers={"Idempotency-Key": "evaluation-live-001"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_operator_can_govern_baseline_and_bad_case_retest(tmp_path) -> None:
    application = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: actor("operator")
    sessions: sessionmaker[Session] = sessionmaker(
        bind=application.state.test_engine,
        expire_on_commit=False,
    )
    source_run_id = _seed_completed_run(sessions, status="FAILED")

    with TestClient(application) as client:
        baseline = client.post(
            f"/api/v1/evaluations/runs/{source_run_id}/baseline",
            json={"confirm": True},
        )
        baselines = client.get("/api/v1/evaluations/baselines")
        archived = client.post(
            f"/api/v1/evaluations/runs/{source_run_id}/cases/"
            "safety_accept_bounded_fields/bad-case",
            json={
                "severity": "HIGH",
                "safe_issue_summary": "安全Schema固定案例发生回归",
            },
        )
        cases = client.get(
            f"/api/v1/evaluations/runs/{source_run_id}/cases",
            params={"status": "FAILED", "category": "SAFETY_SCHEMA"},
        )
        bad_case_id = archived.json()["bad_case_id"]
        started = client.patch(
            f"/api/v1/evaluations/bad-cases/{bad_case_id}",
            json={
                "action": "START_WORK",
                "expected_version": archived.json()["version"],
                "assignee_id": "EMP-OWNER",
            },
        )
        ready = client.patch(
            f"/api/v1/evaluations/bad-cases/{bad_case_id}",
            json={
                "action": "READY_FOR_RETEST",
                "expected_version": started.json()["version"],
                "remediation_note": "修复边界并增加回归测试",
                "target_fix_version": "2026.7.1",
            },
        )
        retest = client.post(
            f"/api/v1/evaluations/bad-cases/{bad_case_id}/retest",
            json={"expected_version": ready.json()["version"]},
            headers={"Idempotency-Key": "bad-case-retest-api-001"},
        )

        _complete_retest(
            sessions,
            retest.json()["run_id"],
            status="PASSED",
        )
        verified = client.post(
            f"/api/v1/evaluations/bad-cases/{bad_case_id}/verify",
            json={"expected_version": ready.json()["version"] + 1},
        )
        closed = client.post(
            f"/api/v1/evaluations/bad-cases/{bad_case_id}/close",
            json={"expected_version": verified.json()["version"]},
        )
        listed = client.get("/api/v1/evaluations/bad-cases")

    assert baseline.status_code == 200
    assert baselines.json()["items"][0]["is_current"] is True
    assert archived.status_code == 201
    assert cases.json()["total"] == 1
    assert cases.json()["items"][0]["case_id"] == "safety_accept_bounded_fields"
    assert "message" not in cases.json()["items"][0]
    assert retest.status_code == 200
    assert verified.json()["status"] == "VERIFIED"
    assert closed.json()["status"] == "CLOSED"
    assert listed.json()["total"] == 1


def _seed_completed_run(
    sessions: sessionmaker[Session],
    *,
    status: str,
) -> str:
    catalog = EvaluationSuiteCatalog(Path(__file__).resolve().parents[3])
    summary = catalog.describe("v2_agent_rag")
    run_id = str(uuid4())
    with sessions.begin() as session:
        session.add(
            EvaluationRun(
                id=run_id,
                suite_key=summary.suite_key,
                suite_name=summary.suite_name,
                suite_version=summary.suite_version,
                content_digest=summary.content_digest,
                selected_case_ids=["safety_accept_bounded_fields"],
                mode="CONTRACT_ONLY",
                status="COMPLETED",
                created_by="operator",
                command_key=str(uuid4()),
                total_case_count=1,
                completed_case_count=1,
                failed_case_count=int(status in {"FAILED", "ERROR"}),
                passed_case_count=int(status == "PASSED"),
                evaluation_rules_version="2026.1",
                safe_configuration_snapshot={},
            )
        )
        session.add(
            EvaluationCaseResultRecord(
                id=str(uuid4()),
                run_id=run_id,
                case_id="safety_accept_bounded_fields",
                category="SAFETY_SCHEMA",
                status=status,
                duration_ms=1,
                call_count=0,
            )
        )
    return run_id


def _complete_retest(
    sessions: sessionmaker[Session],
    run_id: str,
    *,
    status: str,
) -> None:
    with sessions.begin() as session:
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.status = "COMPLETED"
        run.completed_case_count = 1
        run.passed_case_count = int(status == "PASSED")
        run.failed_case_count = int(status in {"FAILED", "ERROR"})
        session.add(
            EvaluationCaseResultRecord(
                id=str(uuid4()),
                run_id=run_id,
                case_id="safety_accept_bounded_fields",
                category="SAFETY_SCHEMA",
                status=status,
                duration_ms=1,
                call_count=0,
            )
        )
