from fastapi.testclient import TestClient

from app.api.dependencies import CurrentActor, get_current_actor
from app.core.config import Settings
from app.main import create_app
from tests.workflow.test_human_review import build_service


class ReviewRuntime:
    def __init__(self, service, engine) -> None:
        self.human_review = service
        self.engine = engine

    def close(self) -> None:
        self.engine.dispose()


def test_human_review_api_requires_operator_and_records_audited_note(tmp_path):
    service, _, engine = build_service(tmp_path)
    app = create_app(
        settings=Settings(database_url="configured-for-test"),
        runtime_factory=lambda settings: ReviewRuntime(service, engine),
    )
    app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001", roles=frozenset({"employee"})
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/human-reviews").status_code == 403
        assert client.post(
            "/api/v1/human-reviews/review-run/decisions",
            json={
                "expected_workflow_version": 2,
                "outcome": "NOTE",
                "public_summary": "正在人工核对外部系统的最终状态。",
            },
        ).status_code == 403
        app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-OPERATOR", roles=frozenset({"operator"})
        )
        response = client.get("/api/v1/human-reviews")
        assert response.status_code == 200
        assert response.json()[0]["workflow_run_id"] == "review-run"
        assert response.json()[0]["can_confirm_success"] is True
        note = client.post(
            "/api/v1/human-reviews/review-run/decisions",
            json={
                "expected_workflow_version": 2,
                "outcome": "NOTE",
                "public_summary": "正在人工核对外部系统的最终状态。",
            },
        )
        assert note.status_code == 200
        assert note.json()["state"] == "WAITING_HUMAN"
