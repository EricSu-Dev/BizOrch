from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.models import ActionExecutionRecord
from app.agents.contracts import AgentIntent, SupervisorAnswer, SupervisorPlan
from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.orchestrator import MultiAgentService
from app.agents.scenarios import ScenarioRegistry
from app.agents.supervisor import SupervisorAgent
from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.workbench import ApprovalWorkbenchService
from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipment,
    EnterpriseOpsEquipmentCriticality,
    EnterpriseOpsMaintenanceHistory,
)
from app.knowledge.contracts import (
    KnowledgeCitation,
    KnowledgeSearchResult,
    KnowledgeTrustLevel,
)
from app.persistence.base import Base
from app.scenarios.equipment_maintenance.agent import (
    EquipmentMaintenanceScenarioHandler,
    MaintenanceDomainAgent,
)
from app.scenarios.equipment_maintenance.commands import (
    MaintenanceRequestCommandService,
)
from app.scenarios.equipment_maintenance.composition import (
    build_maintenance_execution_service,
)
from app.scenarios.equipment_maintenance.context import (
    MaintenanceRequestContextResolver,
)
from app.tickets.contracts import TicketStatus
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.state import WorkflowState
from tests.scenarios.equipment_maintenance.test_execution import (
    FakeMaintenanceEnterpriseClient,
)


class MaintenanceModel:
    model = "fixed-maintenance-e2e"

    def __init__(self, *, dangerous: bool = False) -> None:
        self._dangerous = dangerous

    def generate(self, response_model, *, system_prompt: str, user_prompt: str):
        if response_model is SupervisorPlan:
            return SupervisorPlan(
                intent=AgentIntent.MAINTENANCE_REQUEST,
                scenario_key="equipment_maintenance",
                knowledge_space="equipment_maintenance",
                rewritten_query="设备异常振动报修与现场安全",
                scenario_payload={
                    "equipment_code": "PRESS-001",
                    "fault_description": "持续异响并伴随明显振动",
                    "observed_at": "2026-07-19T10:30:00+08:00",
                    "production_impact": "SLOWDOWN",
                    "safety_observation": (
                        "设备冒烟且有人员受伤"
                        if self._dangerous
                        else "未观察到冒烟、火花、泄漏或人员受伤"
                    ),
                    "business_reason": "停机检修并恢复稳定生产",
                },
            )
        if response_model is SupervisorAnswer:
            return SupervisorAnswer(reply="设备报修已按企业安全流程受理。")
        raise AssertionError(response_model)


class EquipmentKnowledge:
    def search(self, **kwargs) -> KnowledgeSearchResult:
        return KnowledgeSearchResult(
            retrieval_id="maintenance-retrieval",
            knowledge_space=kwargs["knowledge_space"],
            query=kwargs["query"],
            citations=(
                KnowledgeCitation(
                    document_id="maintenance-policy",
                    chunk_id="maintenance-policy-1",
                    chunk_index=0,
                    title="设备故障报告与维修工单管理办法",
                    source_uri=(
                        "demo-maintenance://operations/equipment-fault-reporting"
                    ),
                    version_label="2026.1",
                    source_department="设备管理部",
                    trust_level=KnowledgeTrustLevel.AUTHORITATIVE,
                    excerpt="正常停机检修方案由设备责任人审批。",
                    vector_score=0.9,
                    lexical_score=0.9,
                    combined_score=0.9,
                ),
            ),
        )


class FullMaintenanceEnterpriseClient(FakeMaintenanceEnterpriseClient):
    def query_equipment(self, equipment_code: str) -> EnterpriseOpsEquipment:
        return EnterpriseOpsEquipment(
            equipment_id="equipment-press-001",
            equipment_code=equipment_code,
            name="1600T冲压机",
            site_code="PLANT-A",
            workshop_code="WS-01",
            production_line="PRESS-LINE-01",
            criticality=EnterpriseOpsEquipmentCriticality.HIGH,
            status=self.status,
            responsible_manager_id="EMP-MAINT-MANAGER",
            version=self.version,
            updated_at=datetime(2026, 7, 19, 8, 0, tzinfo=UTC),
        )

    def query_maintenance_history(
        self,
        equipment_code: str,
        *,
        limit: int = 10,
    ) -> tuple[EnterpriseOpsMaintenanceHistory, ...]:
        return ()


def build_vertical_slice(tmp_path, *, dangerous: bool = False):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'v2-e2e.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    checkpoint = SqliteCheckpointStore(tmp_path / "v2-e2e-checkpoints.db")
    enterprise = FullMaintenanceEnterpriseClient()
    tickets = TicketProjectionService(sessions)
    commands = MaintenanceRequestCommandService(
        sessions,
        tickets,
        approval_checkpoint=ApprovalCheckpointCoordinator(
            sessions,
            checkpoint.saver,
        ),
        execution_service=build_maintenance_execution_service(
            sessions,
            enterprise,
        ),
    )
    handler = EquipmentMaintenanceScenarioHandler(
        MaintenanceDomainAgent(MaintenanceRequestContextResolver(enterprise)),
        commands,
    )
    service = MultiAgentService(
        SupervisorAgent(MaintenanceModel(dangerous=dangerous)),
        KnowledgeAgent(EquipmentKnowledge()),
        ScenarioRegistry((handler,)),
    )
    return sessions, checkpoint, enterprise, tickets, commands, service


def test_natural_language_to_approved_verified_work_order_vertical_slice(tmp_path) -> None:
    sessions, checkpoint, enterprise, tickets, commands, service = (
        build_vertical_slice(tmp_path)
    )
    try:
        intake = service.handle(
            "PRESS-001持续异响振动并导致生产降速，请安排停机检修。",
            actor_id="EMP-2001",
            actor_roles=frozenset({"employee"}),
            request_id="maintenance-v2-e2e",
        )

        assert intake.intent is AgentIntent.MAINTENANCE_REQUEST
        assert intake.scenario_key == "equipment_maintenance"
        assert intake.knowledge is not None
        assert intake.knowledge.citations[0].source_department == "设备管理部"
        assert intake.workflow is not None
        assert intake.workflow.workflow_state is WorkflowState.WAITING_APPROVAL
        assert intake.workflow.checkpoint_pending
        assert intake.workflow.approval_id

        approval = ApprovalWorkbenchService(sessions).get_for_approver(
            intake.workflow.approval_id,
            approver_id="EMP-MAINT-MANAGER",
        )
        assert approval.status is ApprovalStatus.PENDING
        assert approval.action_type == "create_maintenance_work_order"
        assert approval.parameters["equipment_code"] == "PRESS-001"
        assert approval.parameters["expected_equipment_version"] == 4

        completed = commands.decide(
            workflow_run_id=intake.workflow.workflow_run_id,
            approval_id=intake.workflow.approval_id,
            expected_workflow_version=intake.workflow.workflow_version,
            actor_id="EMP-MAINT-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
            comment="设备责任人确认停机检修安排",
        )

        assert completed.workflow_state is WorkflowState.COMPLETED
        assert completed.execution_outcome == "SUCCEEDED"
        assert enterprise.write_calls == 1
        assert len(enterprise.orders) == 1
        order = next(iter(enterprise.orders.values()))
        assert order.equipment_code == "PRESS-001"
        assert order.requester_id == "EMP-2001"
        assert enterprise.status.value == "MAINTENANCE_PENDING"
        ticket = tickets.list_for_requester("EMP-2001")[0]
        assert ticket.scenario_key == "equipment_maintenance"
        assert ticket.status is TicketStatus.RESOLVED
        assert ticket.workflow_state == WorkflowState.COMPLETED.value
        with sessions() as session:
            assert session.scalar(select(func.count(ActionExecutionRecord.id))) == 1
    finally:
        checkpoint.close()


def test_explicit_danger_stops_before_approval_and_write(tmp_path) -> None:
    _, checkpoint, enterprise, _, _, service = build_vertical_slice(
        tmp_path,
        dangerous=True,
    )
    try:
        result = service.handle(
            "PRESS-001正在冒烟并且有人受伤，请立即处理。",
            actor_id="EMP-2001",
            actor_roles=frozenset({"employee"}),
            request_id="maintenance-danger-e2e",
        )

        assert result.workflow is not None
        assert result.workflow.workflow_state is WorkflowState.WAITING_HUMAN
        assert result.workflow.approval_id is None
        assert enterprise.write_calls == 0
        assert "DIRECT_SAFETY_HAZARD" in result.scenario_summary["reason_codes"]
        assert "FOLLOW_SITE_EMERGENCY_RULES" in result.scenario_summary["reason_codes"]
    finally:
        checkpoint.close()
