"""Application-level resource composition and shutdown lifecycle."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Engine

from app.auth.service import AuthService
from app.auth.avatar_storage import build_avatar_object_storage
from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.llm import DeepSeekJsonModel
from app.agents.orchestrator import MultiAgentService
from app.agents.scenarios import ScenarioRegistry
from app.agents.supervisor import SupervisorAgent
from app.approval.workbench import ApprovalWorkbenchService
from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.dispatcher import ApprovalDecisionDispatcher
from app.approval.sequence_checkpoint import (
    ApprovalSequenceCheckpointCoordinator,
)
from app.core.config import Settings
from app.knowledge.embeddings import DashScopeEmbeddings
from app.knowledge.service import KnowledgeService
from app.knowledge.storage import KnowledgeFileStorage
from app.knowledge.vector_store import ChromaKnowledgeVectorStore
from app.persistence.database import build_engine, build_session_factory
from app.scenarios.access_management.commands import AccessRequestCommandService
from app.scenarios.access_management.agent import (
    AccessDomainAgent,
    AccessManagementScenarioHandler,
)
from app.scenarios.access_management.composition import (
    build_access_workflow,
    build_mcp_enterprise_clients,
)
from app.scenarios.access_management.context import AccessRequestContextResolver
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
from app.scenarios.equipment_maintenance.composition import (
    build_maintenance_execution_service,
)
from app.scenarios.employee_lifecycle.agent import (
    EmployeeLifecycleDomainAgent,
    EmployeeLifecycleScenarioHandler,
)
from app.scenarios.employee_lifecycle.commands import (
    EmployeeLifecycleIntakeService,
)
from app.scenarios.employee_lifecycle.context import (
    EmployeeLifecycleContextResolver,
)
from app.scenarios.employee_lifecycle.composition import (
    build_employee_lifecycle_execution_service,
)
from app.scenarios.procurement.agent import (
    ProcurementDomainAgent,
    ProcurementScenarioHandler,
)
from app.scenarios.procurement.commands import ProcurementRequestCommandService
from app.scenarios.procurement.composition import (
    build_procurement_execution_service,
)
from app.scenarios.procurement.context import ProcurementRequestContextResolver
from app.scenarios.procurement.policy import (
    AuthApprovalEligibility,
    ProcurementPolicyEngine,
)
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.query import WorkflowProgressQueryService
from app.workflow.human_review import HumanReviewService
from app.conversations.service import ConversationService


@dataclass
class ApplicationRuntime:
    """Own long-lived resources shared by API requests in one process."""

    engine: Engine
    checkpoint_store: SqliteCheckpointStore
    access_requests: AccessRequestCommandService
    maintenance_requests: MaintenanceRequestCommandService
    employee_lifecycle_requests: EmployeeLifecycleIntakeService
    procurement_requests: ProcurementRequestCommandService
    tickets: TicketProjectionService
    workflow_progress: WorkflowProgressQueryService
    human_review: HumanReviewService
    approval_workbench: ApprovalWorkbenchService
    approval_decisions: ApprovalDecisionDispatcher
    knowledge: KnowledgeService
    multi_agent: MultiAgentService
    conversations: ConversationService
    auth: AuthService

    def close(self) -> None:
        """Release checkpoint and SQL connection pools during ASGI shutdown."""
        self.checkpoint_store.close()
        self.engine.dispose()


def build_runtime(settings: Settings) -> ApplicationRuntime:
    """Compose production adapters without creating or migrating tables."""
    engine = build_engine(settings.database_url, echo=settings.debug)
    session_factory = build_session_factory(engine)
    checkpoint_store: SqliteCheckpointStore | None = None
    try:
        workflow, checkpoint_store = build_access_workflow(
            session_factory,
            mcp_endpoint=settings.enterprise_ops_mcp_url,
            mcp_read_token=settings.enterprise_ops_mcp_read_token.get_secret_value(),
            mcp_action_gateway_token=(
                settings.enterprise_ops_mcp_action_gateway_token.get_secret_value()
            ),
            checkpoint_path=settings.checkpoint_path,
        )
        full_client, read_only_client = build_mcp_enterprise_clients(
            settings.enterprise_ops_mcp_url,
            read_token=settings.enterprise_ops_mcp_read_token.get_secret_value(),
            action_gateway_token=(
                settings.enterprise_ops_mcp_action_gateway_token.get_secret_value()
            ),
        )
        tickets = TicketProjectionService(session_factory)
        workflow_progress = WorkflowProgressQueryService(session_factory)
        human_review = HumanReviewService(session_factory, tickets)
        context_resolver = AccessRequestContextResolver(read_only_client)
        commands = AccessRequestCommandService(
            workflow,
            context_resolver,
            tickets,
        )
        maintenance_checkpoint = ApprovalCheckpointCoordinator(
            session_factory,
            checkpoint_store.saver,
        )
        procurement_checkpoint = ApprovalSequenceCheckpointCoordinator(
            session_factory,
            checkpoint_store.saver,
        )
        maintenance_commands = MaintenanceRequestCommandService(
            session_factory,
            tickets,
            approval_checkpoint=maintenance_checkpoint,
            execution_service=build_maintenance_execution_service(
                session_factory,
                full_client,
            ),
        )
        maintenance_context = MaintenanceRequestContextResolver(read_only_client)
        employee_lifecycle_commands = EmployeeLifecycleIntakeService(
            session_factory,
            tickets,
            approval_checkpoint=maintenance_checkpoint,
            execution_service=build_employee_lifecycle_execution_service(
                session_factory,
                full_client,
            ),
        )
        employee_lifecycle_context = EmployeeLifecycleContextResolver(
            read_only_client
        )
        procurement_commands = ProcurementRequestCommandService(
            session_factory,
            tickets,
            policy_engine=ProcurementPolicyEngine(
                AuthApprovalEligibility(session_factory)
            ),
            approval_checkpoint=procurement_checkpoint,
            execution_service=build_procurement_execution_service(
                session_factory,
                full_client,
            ),
        )
        procurement_context = ProcurementRequestContextResolver(read_only_client)
        auth = AuthService(
            session_factory,
            session_ttl=timedelta(hours=settings.auth_session_ttl_hours),
            avatar_storage=build_avatar_object_storage(settings),
            avatar_prefix=settings.oss_avatar_prefix,
        )
        approval_workbench = ApprovalWorkbenchService(session_factory)
        approval_decisions = ApprovalDecisionDispatcher(
            session_factory,
            {
                "access_management": commands,
                "equipment_maintenance": maintenance_commands,
                "employee_lifecycle": employee_lifecycle_commands,
                "procurement": procurement_commands,
            },
        )
        knowledge = KnowledgeService(
            session_factory,
            DashScopeEmbeddings(
                settings.dashscope_api_key.get_secret_value()
                if settings.dashscope_api_key
                else None
            ),
            ChromaKnowledgeVectorStore(settings.chroma_path),
            file_storage=KnowledgeFileStorage(settings.knowledge_upload_path),
        )
        multi_agent = MultiAgentService(
            SupervisorAgent(
                DeepSeekJsonModel(
                    settings.deepseek_api_key.get_secret_value()
                    if settings.deepseek_api_key
                    else None,
                    base_url=settings.deepseek_base_url,
                    model=settings.deepseek_model,
                )
            ),
            KnowledgeAgent(knowledge),
            ScenarioRegistry(
                (
                    AccessManagementScenarioHandler(
                        AccessDomainAgent(context_resolver),
                        commands,
                    ),
                    EquipmentMaintenanceScenarioHandler(
                        MaintenanceDomainAgent(maintenance_context),
                        maintenance_commands,
                    ),
                    EmployeeLifecycleScenarioHandler(
                        EmployeeLifecycleDomainAgent(employee_lifecycle_context),
                        employee_lifecycle_commands,
                    ),
                    ProcurementScenarioHandler(
                        ProcurementDomainAgent(procurement_context),
                        procurement_commands,
                    ),
                )
            ),
        )
        conversations = ConversationService(session_factory, multi_agent)
        return ApplicationRuntime(
            engine=engine,
            checkpoint_store=checkpoint_store,
            access_requests=commands,
            maintenance_requests=maintenance_commands,
            employee_lifecycle_requests=employee_lifecycle_commands,
            procurement_requests=procurement_commands,
            tickets=tickets,
            workflow_progress=workflow_progress,
            human_review=human_review,
            approval_workbench=approval_workbench,
            approval_decisions=approval_decisions,
            knowledge=knowledge,
            multi_agent=multi_agent,
            conversations=conversations,
            auth=auth,
        )
    except Exception:
        if checkpoint_store is not None:
            checkpoint_store.close()
        engine.dispose()
        raise
