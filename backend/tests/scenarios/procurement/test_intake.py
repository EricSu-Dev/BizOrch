from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.contracts import AgentIntent, SupervisorPlan
from app.agents.scenarios import ScenarioActor
from app.persistence.base import Base
from app.scenarios.procurement.agent import (
    ProcurementDomainAgent,
    ProcurementScenarioHandler,
)
from app.scenarios.procurement.commands import ProcurementRequestCommandService
from app.scenarios.procurement.context import ProcurementRequestContextResolver
from app.tickets.service import TicketProjectionService
from app.workflow.state import WorkflowState
from tests.scenarios.procurement.test_context import FakeProcurementClient


def test_procurement_intake_recovers_missing_information_without_policy_or_write():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)
    commands = ProcurementRequestCommandService(
        sessions, TicketProjectionService(sessions)
    )
    handler = ProcurementScenarioHandler(
        ProcurementDomainAgent(
            ProcurementRequestContextResolver(FakeProcurementClient())
        ),
        commands,
    )
    actor = ScenarioActor(actor_id="EMP-1001", roles=frozenset({"employee"}))
    started = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.OFFICE_PROCUREMENT_REQUEST,
            scenario_key="procurement",
            knowledge_space="procurement",
            scenario_payload={"cost_center_code": "CC-SALES-EAST-001"},
        ),
        actor=actor,
        request_id="procurement-intake-1",
        workflow_run_id=None,
    )
    assert started.workflow.workflow_state is WorkflowState.WAITING_USER
    assert "items" in started.workflow.missing_fields

    resumed = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.OFFICE_PROCUREMENT_REQUEST,
            scenario_key="procurement",
            knowledge_space="procurement",
            scenario_payload={
                "items": [
                    {
                        "item_name": "Display",
                        "item_category": "OFFICE_EQUIPMENT",
                        "quantity": 1,
                    }
                ],
                "estimated_total_amount": "2600.00",
                "desired_date": "2026-08-05",
                "delivery_location_code": "SHANGHAI-HQ",
                "business_reason": "Project delivery support",
            },
        ),
        actor=actor,
        request_id="ignored-on-resume",
        workflow_run_id=started.workflow.workflow_run_id,
    )

    assert resumed.workflow.workflow_state is WorkflowState.WAITING_HUMAN
    assert resumed.workflow.missing_fields == ()
    assert resumed.scenario_summary["intake_stage"] == "READY_FOR_DETERMINISTIC_POLICY"
    assert resumed.workflow.approval_id is None
    assert resumed.workflow.action_id is None


def test_procurement_intake_recovers_explicit_chinese_procurement_fields():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)
    commands = ProcurementRequestCommandService(
        sessions, TicketProjectionService(sessions)
    )
    handler = ProcurementScenarioHandler(
        ProcurementDomainAgent(
            ProcurementRequestContextResolver(FakeProcurementClient())
        ),
        commands,
    )
    result = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.OFFICE_PROCUREMENT_REQUEST,
            scenario_key="procurement",
            knowledge_space="procurement",
            scenario_payload={"application_code": "CRM"},
        ),
        actor=ScenarioActor(actor_id="EMP-1001", roles=frozenset({"employee"})),
        request_id="procurement-intake-explicit-fields",
        workflow_run_id=None,
        message=(
            "申请采购办公用品：A4打印纸2箱，类别为办公耗材，预计金额260元，"
            "使用成本中心 CC-SALES-EAST-001，期望到货日期为2026-08-05，"
            "送到上海总部行政前台，用于新员工入职办公区补充。"
        ),
    )

    assert result.workflow.workflow_state is WorkflowState.WAITING_HUMAN
    assert result.workflow.missing_fields == ()
    assert result.scenario_summary["cost_center_found"] is True
