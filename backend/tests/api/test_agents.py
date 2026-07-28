from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionGatewayOutcome, ActionGatewayResult
from app.agents.contracts import (
    AgentIntent,
    SupervisorAnswer,
    SupervisorPlan,
)
from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.llm import AgentModelExecutionError, DeepSeekJsonModel
from app.agents.orchestrator import MultiAgentService
from app.agents.scenarios import ScenarioRegistry
from app.agents.supervisor import SupervisorAgent
from app.api.dependencies import CurrentActor, get_current_actor
from app.core.config import Settings
from app.conversations.service import ConversationService
from app.knowledge.contracts import (
    KnowledgeCitation,
    KnowledgeSearchResult,
    KnowledgeTrustLevel,
)
from app.main import create_app
from app.persistence.base import Base
from app.scenarios.access_management.commands import AccessRequestCommandService
from app.scenarios.access_management.agent import (
    AccessDomainAgent,
    AccessManagementScenarioHandler,
)
from app.scenarios.access_management.context import AccessRequestContextResolver
from app.scenarios.access_management.execution import AccessRequestExecutionService
from app.scenarios.access_management.workflow import AccessRequestWorkflow
from app.scenarios.equipment_maintenance.agent import (
    EquipmentMaintenanceScenarioHandler,
    MaintenanceDomainAgent,
)
from app.scenarios.equipment_maintenance.commands import (
    MaintenanceRequestCommandService,
)
from app.scenarios.equipment_maintenance.context import (
    MaintenanceRequestContextResolver,
)
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore


class UnusedGateway:
    def execute(self, proposal, **kwargs) -> ActionGatewayResult:
        return ActionGatewayResult(
            outcome=ActionGatewayOutcome.SUCCEEDED,
            action_id=proposal.action_id,
            action_version=proposal.version,
            idempotency_key=kwargs["idempotency_key"],
        )


class FakeEnterpriseClient:
    def __init__(
        self,
        *,
        missing_equipment: bool = False,
        missing_equipment_code: str | None = None,
    ) -> None:
        self.missing_equipment = missing_equipment
        self.missing_equipment_code = missing_equipment_code

    def query_employee(self, employee_id: str):
        from app.integrations.enterprise_ops import EnterpriseOpsEmployee

        return EnterpriseOpsEmployee(
            employee_id=employee_id,
            display_name="Employee",
            department_code="ENG",
            manager_id="EMP-MANAGER",
            active=True,
        )

    def query_application(self, application_code: str):
        from app.integrations.enterprise_ops import EnterpriseOpsApplication

        return EnterpriseOpsApplication(
            application_code=application_code,
            display_name="CRM",
            active=True,
            allowed_role_codes=("read_only",),
        )

    def query_user_access(self, employee_id: str):
        return ()

    def query_equipment(self, equipment_code: str):
        from datetime import UTC, datetime

        from app.integrations.enterprise_ops import (
            EnterpriseOpsEquipment,
            EnterpriseOpsEquipmentCriticality,
            EnterpriseOpsEquipmentStatus,
            EnterpriseOpsResourceNotFoundError,
        )

        if self.missing_equipment or equipment_code == self.missing_equipment_code:
            raise EnterpriseOpsResourceNotFoundError(equipment_code)

        return EnterpriseOpsEquipment(
            equipment_id="equipment-1",
            equipment_code=equipment_code,
            name="1600T冲压机",
            site_code="PLANT-A",
            workshop_code="WS-01",
            production_line="LINE-PRESS",
            criticality=EnterpriseOpsEquipmentCriticality.HIGH,
            status=EnterpriseOpsEquipmentStatus.RUNNING,
            responsible_manager_id="EMP-MAINT-MANAGER",
            version=4,
            updated_at=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
        )

    def query_equipment_status(self, equipment_code: str):
        from datetime import UTC, datetime

        from app.integrations.enterprise_ops import (
            EnterpriseOpsEquipmentStatus,
            EnterpriseOpsEquipmentStatusView,
        )

        return EnterpriseOpsEquipmentStatusView(
            equipment_code=equipment_code,
            status=EnterpriseOpsEquipmentStatus.RUNNING,
            version=4,
            updated_at=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
        )

    def query_maintenance_history(self, equipment_code: str, *, limit: int = 10):
        return ()


class FakeJsonModel:
    model = "fake-json-model"

    def __init__(
        self,
        *,
        inject_employee: bool = False,
        intent: AgentIntent = AgentIntent.ACCESS_REQUEST,
        fail_summary: bool = False,
        staged: bool = False,
        unknown_bound_maintenance: bool = False,
        bad_maintenance_supplement: bool = False,
    ) -> None:
        self.inject_employee = inject_employee
        self.intent = intent
        self.fail_summary = fail_summary
        self.staged = staged
        self.unknown_bound_maintenance = unknown_bound_maintenance
        self.bad_maintenance_supplement = bad_maintenance_supplement

    def generate(self, response_model, *, system_prompt: str, user_prompt: str):
        if response_model is SupervisorPlan:
            if self.bad_maintenance_supplement:
                if "workflow_supplement" in user_prompt:
                    return SupervisorPlan(
                        intent=AgentIntent.MAINTENANCE_REQUEST,
                        scenario_key="equipment_maintenance",
                        knowledge_space="equipment_maintenance",
                        scenario_payload={
                            "fault_description": "模型错误生成的故障描述",
                            "production_impact": "SLOWDOWN",
                        },
                    )
                return SupervisorPlan(
                    intent=AgentIntent.MAINTENANCE_REQUEST,
                    scenario_key="equipment_maintenance",
                    knowledge_space="equipment_maintenance",
                    scenario_payload={
                        "equipment_code": "PRESS-001",
                        "fault_description": "异常振动",
                        "production_impact": "SLOWDOWN",
                    },
                )
            if self.unknown_bound_maintenance:
                if "workflow_supplement" in user_prompt:
                    return SupervisorPlan(
                        intent=AgentIntent.UNKNOWN,
                        scenario_payload={},
                    )
                return SupervisorPlan(
                    intent=AgentIntent.MAINTENANCE_REQUEST,
                    scenario_key="equipment_maintenance",
                    knowledge_space="equipment_maintenance",
                    scenario_payload={"fault_description": "设备异常振动"},
                )
            if self.intent is AgentIntent.KNOWLEDGE_QUESTION:
                return SupervisorPlan(
                    intent=self.intent,
                    knowledge_space="access_and_security",
                    rewritten_query="VPN approval policy",
                )
            if self.intent is AgentIntent.MAINTENANCE_REQUEST:
                payload = (
                    {
                        "observed_at": "2026-07-18T08:00:00Z",
                        "production_impact": "SLOWDOWN",
                        "safety_observation": "未观察到直接危险",
                        "business_reason": "停机检查异响来源",
                    }
                    if self.staged and "补充" in user_prompt
                    else {
                        "equipment_code": "PRESS-001",
                        "fault_description": "持续异响并伴随明显振动",
                    }
                )
                return SupervisorPlan(
                    intent=self.intent,
                    scenario_key="equipment_maintenance",
                    knowledge_space="equipment_maintenance",
                    rewritten_query="equipment maintenance policy",
                    scenario_payload=payload,
                )
            payload = (
                {
                    "role_code": "read_only",
                    "duration_days": 30,
                    "business_reason": "Customer project support",
                }
                if self.staged and "补充" in user_prompt
                else {
                    "application_code": "CRM",
                }
                if self.staged
                else {
                    "application_code": "CRM",
                    "role_code": "read_only",
                    "duration_days": 30,
                    "business_reason": "Customer project support",
                }
            )
            if self.inject_employee:
                payload["employee_id"] = "EMP-OTHER"
            return SupervisorPlan(
                intent=AgentIntent.ACCESS_REQUEST,
                scenario_key="access_management",
                knowledge_space="access_and_security",
                rewritten_query="CRM access approval policy",
                scenario_payload=payload,
            )
        if response_model is SupervisorAnswer:
            if self.fail_summary:
                raise AgentModelExecutionError("summary unavailable")
            return SupervisorAnswer(reply="申请已创建，正在等待直属领导审批。")
        raise AssertionError(response_model)


class FakeKnowledgeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs) -> KnowledgeSearchResult:
        self.calls.append(dict(kwargs))
        return KnowledgeSearchResult(
            retrieval_id="retrieval-1",
            knowledge_space=kwargs["knowledge_space"],
            query=kwargs["query"],
            citations=(
                KnowledgeCitation(
                    document_id="document-1",
                    chunk_id="chunk-1",
                    chunk_index=0,
                    title="Access Policy",
                    source_uri="policy://access",
                    version_label="2026.1",
                    source_department="Security",
                    trust_level=KnowledgeTrustLevel.AUTHORITATIVE,
                    excerpt="Manager approval is required.",
                    vector_score=0.9,
                    lexical_score=1.0,
                    combined_score=0.93,
                ),
            ),
        )


class AgentRuntimeHarness:
    def __init__(self, multi_agent, conversations, workflow, checkpoint_store) -> None:
        self.multi_agent = multi_agent
        self.conversations = conversations
        self.workflow = workflow
        self._checkpoint_store = checkpoint_store
        self.closed = False

    def close(self) -> None:
        self._checkpoint_store.close()
        self.closed = True


def build_app(
    tmp_path,
    *,
    model=None,
    knowledge_service=None,
    missing_equipment: bool = False,
    missing_equipment_code: str | None = None,
):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    checkpoint_store = SqliteCheckpointStore(tmp_path / "agents-checkpoint.db")
    workflow = AccessRequestWorkflow(
        sessions,
        AccessRequestExecutionService(sessions, UnusedGateway()),
        checkpoint_store.saver,
    )
    enterprise_client = FakeEnterpriseClient(
        missing_equipment=missing_equipment,
        missing_equipment_code=missing_equipment_code,
    )
    resolver = AccessRequestContextResolver(enterprise_client)
    commands = AccessRequestCommandService(
        workflow,
        resolver,
        TicketProjectionService(sessions),
    )
    maintenance_commands = MaintenanceRequestCommandService(
        sessions,
        TicketProjectionService(sessions),
    )
    service = MultiAgentService(
        SupervisorAgent(model or FakeJsonModel()),
        KnowledgeAgent(knowledge_service or FakeKnowledgeService()),
        ScenarioRegistry(
            (
                AccessManagementScenarioHandler(AccessDomainAgent(resolver), commands),
                EquipmentMaintenanceScenarioHandler(
                    MaintenanceDomainAgent(
                        MaintenanceRequestContextResolver(enterprise_client)
                    ),
                    maintenance_commands,
                ),
            )
        ),
    )
    conversations = ConversationService(sessions, service)
    runtime = AgentRuntimeHarness(service, conversations, workflow, checkpoint_store)
    application = create_app(
        settings=Settings(database_url="configured-for-test"),
        runtime_factory=lambda settings: runtime,
    )
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001",
        roles=frozenset({"employee"}),
    )
    return application, runtime


def test_agent_message_creates_authoritative_access_workflow(tmp_path) -> None:
    application, runtime = build_app(tmp_path)
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={
                "message": "请帮我申请CRM只读权限30天，用于客户项目。",
                "client_message_id": "client-1",
            },
        )

        assert response.status_code == 200
        envelope = response.json()
        assert envelope["client_message_id"] == "client-1"
        assert envelope["conversation_id"]
        body = envelope["agent"]
        assert body["intent"] == "ACCESS_REQUEST"
        assert body["scenario_key"] == "access_management"
        assert body["scenario_summary"]["manager_id"] == "EMP-MANAGER"
        assert body["scenario_summary"]["proposed_action"]["role_code"] == "read_only"
        assert body["knowledge"]["citations"][0]["source_uri"] == "policy://access"
        assert body["workflow"]["workflow_state"] == "WAITING_APPROVAL"
        assert body["workflow"]["ticket_id"]
        run_id = body["workflow"]["workflow_run_id"]
        assert runtime.workflow.request_draft(run_id).employee_id == "EMP-1001"
        assert {item["agent_name"] for item in body["trace"]} >= {
            "supervisor",
            "knowledge",
            "access_domain",
            "orchestrator",
        }

    assert runtime.closed


def test_model_cannot_override_authenticated_employee(tmp_path) -> None:
    application, _ = build_app(tmp_path, model=FakeJsonModel(inject_employee=True))
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "为别人申请CRM权限", "client_message_id": "client-1"},
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "AGENT_MODEL_RESPONSE_INVALID"


def test_maintenance_intent_starts_safe_information_collection(tmp_path) -> None:
    application, _ = build_app(
        tmp_path,
        model=FakeJsonModel(intent=AgentIntent.MAINTENANCE_REQUEST),
    )
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "冲压机需要报修", "client_message_id": "client-1"},
        )

        assert response.status_code == 200
        body = response.json()["agent"]
        assert body["scenario_key"] == "equipment_maintenance"
        assert body["workflow"]["workflow_state"] == "WAITING_USER"
        assert body["scenario_summary"]["equipment_code"] == "PRESS-001"
        assert body["scenario_summary"]["equipment_status"] == "RUNNING"
        assert body["scenario_summary"]["responsible_manager_id"] == (
            "EMP-MAINT-MANAGER"
        )
        assert "create_maintenance_work_order" not in str(body["trace"])


def test_missing_equipment_returns_explicit_business_reply(tmp_path) -> None:
    application, _ = build_app(
        tmp_path,
        model=FakeJsonModel(intent=AgentIntent.MAINTENANCE_REQUEST),
        missing_equipment=True,
    )
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "不存在的设备需要报修", "client_message_id": "client-1"},
        )

        assert response.status_code == 200
        body = response.json()["agent"]
        assert body["reply"] == "设备不存在，请核对编号。"
        assert body["workflow"]["workflow_state"] == "WAITING_USER"
        assert "equipment_code" in body["workflow"]["missing_fields"]
        assert body["scenario_summary"]["reason_codes"] == [
            "EQUIPMENT_NOT_RESOLVED"
        ]
        assert body["workflow"]["approval_id"] is None


def test_bound_maintenance_supplement_overrides_unknown_model_route(tmp_path) -> None:
    application, _ = build_app(
        tmp_path,
        model=FakeJsonModel(unknown_bound_maintenance=True),
        missing_equipment_code="ABC",
    )
    with TestClient(application) as client:
        first = client.post(
            "/api/v1/agent/messages",
            json={"message": "设备异常振动，需要报修", "client_message_id": "m-1"},
        )
        assert first.status_code == 200
        assert first.json()["agent"]["workflow"]["workflow_state"] == "WAITING_USER"

        second = client.post(
            "/api/v1/agent/messages",
            json={
                "message": (
                    "设备编号是abc，发现时间是今天上午，生产影响：生产降速，"
                    "安全观察：异味"
                ),
                "client_message_id": "m-2",
                "conversation_id": first.json()["conversation_id"],
            },
        )

        assert second.status_code == 200
        body = second.json()["agent"]
        assert body["intent"] == "MAINTENANCE_REQUEST"
        assert body["reply"] == "设备不存在，请核对编号。"
        assert body["workflow"]["workflow_state"] == "WAITING_USER"
        assert "equipment_code" in body["workflow"]["missing_fields"]
        assert body["scenario_summary"]["reason_codes"] == [
            "EQUIPMENT_NOT_RESOLVED"
        ]
        assert any(
            item["capability"] == "bound_workflow_routing"
            for item in body["trace"]
        )


def test_maintenance_supplement_recovers_explicit_facts_and_ignores_model_noise(
    tmp_path,
) -> None:
    application, _ = build_app(
        tmp_path,
        model=FakeJsonModel(bad_maintenance_supplement=True),
    )
    with TestClient(application) as client:
        first = client.post(
            "/api/v1/agent/messages",
            json={"message": "设备异常振动，需要报修", "client_message_id": "m-1"},
        )
        assert first.status_code == 200
        first_agent = first.json()["agent"]
        assert first_agent["workflow"]["workflow_state"] == "WAITING_USER"

        second = client.post(
            "/api/v1/agent/messages",
            json={
                "message": "今天 10:30 发现，生产速度下降，暂未发现明显安全风险",
                "client_message_id": "m-2",
                "conversation_id": first.json()["conversation_id"],
            },
        )

        assert second.status_code == 200
        body = second.json()["agent"]
        assert body["workflow"]["workflow_state"] == "WAITING_USER"
        assert body["workflow"]["missing_fields"] == ["business_reason"]
        assert body["scenario_summary"]["reason_codes"] == [
            "MISSING_BUSINESS_REASON"
        ]
        capabilities = {item["capability"] for item in body["trace"]}
        assert "bounded_supplement_field_filtering" in capabilities
        assert "deterministic_supplement_field_recovery" in capabilities


def test_maintenance_follow_up_resumes_the_same_workflow(tmp_path) -> None:
    application, _ = build_app(
        tmp_path,
        model=FakeJsonModel(
            intent=AgentIntent.MAINTENANCE_REQUEST,
            staged=True,
        ),
    )
    with TestClient(application) as client:
        first = client.post(
            "/api/v1/agent/messages",
            json={"message": "冲压机异响需要报修", "client_message_id": "m-1"},
        )
        assert first.status_code == 200
        conversation_id = first.json()["conversation_id"]
        first_workflow = first.json()["agent"]["workflow"]
        assert first_workflow["workflow_state"] == "WAITING_USER"

        second = client.post(
            "/api/v1/agent/messages",
            json={
                "message": "补充发现时间、生产影响、安全观察和检修理由",
                "client_message_id": "m-2",
                "conversation_id": conversation_id,
            },
        )

        assert second.status_code == 200
        second_workflow = second.json()["agent"]["workflow"]
        assert second_workflow["workflow_run_id"] == first_workflow["workflow_run_id"]
        assert second_workflow["workflow_state"] == "WAITING_APPROVAL"
        assert second_workflow["missing_fields"] == []
        assert second_workflow["action_version"] == 1
        assert second_workflow["approval_status"] == "PENDING"
        assert second.json()["agent"]["scenario_summary"]["proposal_preview"] == {
            "action_type": "create_maintenance_work_order",
            "equipment_code": "PRESS-001",
            "equipment_version": 4,
            "production_impact": "SLOWDOWN",
        }


def test_knowledge_intent_uses_only_knowledge_agent(tmp_path) -> None:
    knowledge = FakeKnowledgeService()
    application, _ = build_app(
        tmp_path,
        model=FakeJsonModel(intent=AgentIntent.KNOWLEDGE_QUESTION),
        knowledge_service=knowledge,
    )
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "VPN权限需要谁审批？", "client_message_id": "client-1"},
        )
        assert response.status_code == 200
        body = response.json()["agent"]
        assert body["intent"] == "KNOWLEDGE_QUESTION"
        assert body["knowledge"]["citations"][0]["title"] == "Access Policy"
        assert body["scenario_summary"] is None
        assert body["workflow"] is None
        assert {item["agent_name"] for item in body["trace"]} == {
            "supervisor",
            "knowledge",
        }
        assert knowledge.calls == [
            {
                "query": "VPN approval policy",
                "knowledge_space": "access_and_security",
                "actor_id": "EMP-1001",
                "actor_roles": frozenset({"employee"}),
                "top_k": 5,
            }
        ]


def test_summary_failure_uses_fallback_after_workflow_creation(tmp_path) -> None:
    application, _ = build_app(tmp_path, model=FakeJsonModel(fail_summary=True))
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "申请CRM只读权限30天", "client_message_id": "client-1"},
        )
        assert response.status_code == 200
        body = response.json()["agent"]
        assert body["workflow"]["workflow_state"] == "WAITING_APPROVAL"
        assert "WAITING_APPROVAL" in body["reply"]
        assert body["trace"][-1]["status"] == "FALLBACK"


def test_missing_deepseek_key_returns_stable_service_unavailable(tmp_path) -> None:
    application, _ = build_app(tmp_path, model=DeepSeekJsonModel(None))
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/agent/messages",
            json={"message": "申请CRM权限", "client_message_id": "client-1"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AGENT_SERVICE_UNAVAILABLE"


def test_conversation_resumes_one_workflow_and_replays_idempotently(tmp_path) -> None:
    application, runtime = build_app(tmp_path, model=FakeJsonModel(staged=True))
    with TestClient(application) as client:
        first = client.post(
            "/api/v1/agent/messages",
            json={"message": "申请CRM权限", "client_message_id": "turn-1"},
        )
        assert first.status_code == 200
        conversation_id = first.json()["conversation_id"]
        first_agent = first.json()["agent"]
        assert first_agent["workflow"]["workflow_state"] == "WAITING_USER"
        run_id = first_agent["workflow"]["workflow_run_id"]

        second_payload = {
            "message": "补充：只读权限30天，用于客户项目支持",
            "client_message_id": "turn-2",
            "conversation_id": conversation_id,
        }
        second = client.post("/api/v1/agent/messages", json=second_payload)
        replay = client.post("/api/v1/agent/messages", json=second_payload)

        assert second.status_code == replay.status_code == 200
        second_agent = second.json()["agent"]
        assert second_agent["workflow"]["workflow_state"] == "WAITING_APPROVAL"
        assert second_agent["workflow"]["workflow_run_id"] == run_id
        assert replay.json() == second.json()

        conversations = client.get("/api/v1/conversations")
        assert conversations.status_code == 200
        assert conversations.json()[0]["workflow_run_id"] == run_id
        assert conversations.json()[0]["message_count"] == 4

        messages = client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        assert messages.status_code == 200
        assert [item["sequence"] for item in messages.json()] == [1, 2, 3, 4]
        assert [item["role"] for item in messages.json()] == [
            "USER",
            "ASSISTANT",
            "USER",
            "ASSISTANT",
        ]
        assert runtime.workflow.request_draft(run_id).role_code == "read_only"


def test_conversation_is_hidden_from_another_actor(tmp_path) -> None:
    application, _ = build_app(tmp_path)
    with TestClient(application) as client:
        created = client.post(
            "/api/v1/agent/messages",
            json={"message": "申请CRM权限", "client_message_id": "turn-1"},
        )
        conversation_id = created.json()["conversation_id"]
        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-OTHER",
            roles=frozenset({"employee"}),
        )

        response = client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ACTION_FORBIDDEN"


def test_client_message_id_cannot_be_reused_with_different_content(tmp_path) -> None:
    application, _ = build_app(tmp_path)
    with TestClient(application) as client:
        first = client.post(
            "/api/v1/agent/messages",
            json={"message": "申请CRM权限", "client_message_id": "same-id"},
        )
        response = client.post(
            "/api/v1/agent/messages",
            json={
                "message": "完全不同的内容",
                "client_message_id": "same-id",
                "conversation_id": first.json()["conversation_id"],
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "STATE_CONFLICT"


def test_owner_can_rename_and_hide_conversation_without_deleting_workflow(
    tmp_path,
) -> None:
    application, runtime = build_app(tmp_path)
    with TestClient(application) as client:
        created = client.post(
            "/api/v1/agent/messages",
            json={"message": "申请CRM权限", "client_message_id": "turn-1"},
        )
        conversation_id = created.json()["conversation_id"]
        workflow_run_id = created.json()["agent"]["workflow"]["workflow_run_id"]

        renamed = client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={"title": "  华东客户CRM权限  "},
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "华东客户CRM权限"
        assert client.get("/api/v1/conversations").json()[0]["title"] == "华东客户CRM权限"

        deleted = client.delete(f"/api/v1/conversations/{conversation_id}")
        assert deleted.status_code == 204
        assert client.get("/api/v1/conversations").json() == []
        assert (
            client.get(f"/api/v1/conversations/{conversation_id}/messages").status_code
            == 404
        )
        assert runtime.workflow.request_draft(workflow_run_id).employee_id == "EMP-1001"


def test_other_actor_cannot_rename_or_delete_conversation(tmp_path) -> None:
    application, _ = build_app(tmp_path)
    with TestClient(application) as client:
        created = client.post(
            "/api/v1/agent/messages",
            json={"message": "申请CRM权限", "client_message_id": "turn-1"},
        )
        conversation_id = created.json()["conversation_id"]
        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-OTHER",
            roles=frozenset({"employee"}),
        )

        renamed = client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={"title": "越权修改"},
        )
        deleted = client.delete(f"/api/v1/conversations/{conversation_id}")

        assert renamed.status_code == 403
        assert deleted.status_code == 403
