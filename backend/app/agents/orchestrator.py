"""Deterministic capability routing around three logical agents."""

from uuid import uuid4

from app.agents.contracts import (
    AgentIntent,
    AgentTraceEvent,
    MultiAgentResult,
    SupervisorPlan,
)
from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.llm import AgentModelError, AgentModelResponseError
from app.agents.scenarios import (
    ScenarioActor,
    ScenarioHandler,
    ScenarioPayloadValidationError,
    ScenarioRegistry,
    ScenarioRoutingError,
)
from app.agents.supervisor import SupervisorAgent
from app.knowledge.contracts import KnowledgeSearchResult
from app.knowledge.embeddings import EmbeddingConfigurationError
from app.knowledge.service import KnowledgeRetrievalError
from app.knowledge.vector_store import VectorStoreConfigurationError


class MultiAgentOrchestrationError(RuntimeError):
    """Raised when a validated plan violates the bounded routing contract."""


class MultiAgentService:
    """Route explicit capabilities; agents never receive unrestricted tool access."""

    def __init__(
        self,
        supervisor: SupervisorAgent,
        knowledge_agent: KnowledgeAgent,
        scenario_registry: ScenarioRegistry,
    ) -> None:
        self._supervisor = supervisor
        self._knowledge_agent = knowledge_agent
        self._scenario_registry = scenario_registry

    def handle(
        self,
        message: str,
        *,
        actor_id: str,
        actor_roles: frozenset[str],
        request_id: str | None = None,
        workflow_run_id: str | None = None,
        bound_scenario_key: str | None = None,
    ) -> MultiAgentResult:
        resolved_request_id = request_id or str(uuid4())
        plan = self._supervisor.plan(
            message,
            bound_scenario_key=bound_scenario_key,
        )
        trace = [
            AgentTraceEvent(
                agent_name="supervisor",
                capability="intent_and_field_extraction",
                status="SUCCEEDED",
                summary=f"routed intent {plan.intent.value}",
            )
        ]
        try:
            scenario_handler = (
                self._scenario_registry.resolve_key(bound_scenario_key)
                if workflow_run_id and bound_scenario_key
                else self._scenario_registry.resolve(plan)
            )
        except ScenarioRoutingError as exc:
            raise MultiAgentOrchestrationError(str(exc)) from exc
        if scenario_handler is not None and workflow_run_id and bound_scenario_key:
            supported_intents = getattr(
                scenario_handler,
                "intents",
                frozenset({scenario_handler.intent}),
            )
            if (
                plan.intent not in supported_intents
                or plan.scenario_key != bound_scenario_key
            ):
                corrected_intent = (
                    next(iter(supported_intents))
                    if len(supported_intents) == 1
                    else plan.intent
                )
                plan = plan.model_copy(
                    update={
                        "intent": corrected_intent,
                        "scenario_key": bound_scenario_key,
                        "knowledge_space": scenario_handler.knowledge_space,
                    }
                )
                trace.append(
                    AgentTraceEvent(
                        agent_name="orchestrator",
                        capability="bound_workflow_routing",
                        status="CORRECTED",
                        summary=(
                            "kept supplement on the authoritative workflow scenario"
                        ),
                    )
                )
        if scenario_handler is not None:
            return self._handle_scenario(
                request_id=resolved_request_id,
                message=message,
                plan=plan,
                handler=scenario_handler,
                actor_id=actor_id,
                actor_roles=actor_roles,
                trace=trace,
                workflow_run_id=workflow_run_id,
            )
        if plan.intent not in (AgentIntent.KNOWLEDGE_QUESTION, AgentIntent.UNKNOWN):
            raise MultiAgentOrchestrationError(
                f"scenario intent is not registered: {plan.intent.value}"
            )
        if plan.intent is AgentIntent.KNOWLEDGE_QUESTION:
            return self._handle_knowledge(
                request_id=resolved_request_id,
                message=message,
                plan=plan,
                actor_id=actor_id,
                actor_roles=actor_roles,
                trace=trace,
            )
        reply = self._safe_reply(
            message=message,
            facts={
                "intent": AgentIntent.UNKNOWN.value,
                "supported_scenarios": (
                    self._scenario_registry.supported_scenario_keys
                ),
            },
            fallback=(
                "目前支持企业系统权限申请、工业设备报修和企业知识问答。"
            ),
            trace=trace,
        )
        return MultiAgentResult(
            request_id=resolved_request_id,
            intent=plan.intent,
            reply=reply,
            trace=tuple(trace),
        )

    def _handle_scenario(
        self,
        *,
        request_id: str,
        message: str,
        plan: SupervisorPlan,
        handler: ScenarioHandler,
        actor_id: str,
        actor_roles: frozenset[str],
        trace: list[AgentTraceEvent],
        workflow_run_id: str | None,
    ) -> MultiAgentResult:
        try:
            actor = ScenarioActor(actor_id=actor_id, roles=actor_roles)
            scenario = handler.handle(
                plan,
                actor=actor,
                request_id=request_id,
                workflow_run_id=workflow_run_id,
                message=message,
            )
        except ScenarioPayloadValidationError as exc:
            raise AgentModelResponseError(
                "supervisor returned invalid scenario fields"
            ) from exc
        trace.extend(scenario.trace)
        resolved_intent = scenario.resolved_intent or plan.intent
        knowledge = self._optional_scenario_knowledge(
            query=plan.rewritten_query or message,
            knowledge_space=handler.knowledge_space,
            actor_id=actor_id,
            actor_roles=actor_roles,
            trace=trace,
        )
        facts = {
            "intent": resolved_intent.value,
            "scenario_key": scenario.scenario_key,
            "scenario": scenario.scenario_summary,
            "workflow": scenario.workflow.model_dump(mode="json"),
            "citations": self._citation_facts(knowledge),
        }
        reply = scenario.authoritative_reply or self._safe_reply(
            message=message,
            facts=facts,
            fallback=scenario.fallback_reply,
            trace=trace,
        )
        return MultiAgentResult(
            request_id=request_id,
            intent=resolved_intent,
            reply=reply,
            scenario_key=scenario.scenario_key,
            knowledge=knowledge,
            scenario_summary=scenario.scenario_summary,
            workflow=scenario.workflow,
            trace=tuple(trace),
        )

    def _handle_knowledge(
        self,
        *,
        request_id: str,
        message: str,
        plan: SupervisorPlan,
        actor_id: str,
        actor_roles: frozenset[str],
        trace: list[AgentTraceEvent],
    ) -> MultiAgentResult:
        if not plan.knowledge_space or not plan.rewritten_query:
            raise AgentModelResponseError(
                "knowledge plan lacks knowledge_space or rewritten_query"
            )
        knowledge = self._knowledge_agent.search(
            query=plan.rewritten_query,
            knowledge_space=plan.knowledge_space,
            actor_id=actor_id,
            actor_roles=actor_roles,
        )
        trace.append(
            AgentTraceEvent(
                agent_name="knowledge",
                capability="authorized_hybrid_retrieval",
                status="SUCCEEDED",
                summary=f"returned {len(knowledge.citations)} citations",
            )
        )
        reply = self._safe_reply(
            message=message,
            facts={
                "intent": plan.intent.value,
                "citations": self._citation_facts(knowledge),
            },
            fallback=(
                "未检索到可引用的有效知识。"
                if not knowledge.citations
                else "已找到相关企业知识，请查看所附来源。"
            ),
            trace=trace,
        )
        return MultiAgentResult(
            request_id=request_id,
            intent=plan.intent,
            reply=reply,
            knowledge=knowledge,
            trace=tuple(trace),
        )

    def _optional_scenario_knowledge(
        self,
        *,
        query: str,
        knowledge_space: str,
        actor_id: str,
        actor_roles: frozenset[str],
        trace: list[AgentTraceEvent],
    ) -> KnowledgeSearchResult | None:
        try:
            result = self._knowledge_agent.search(
                query=query,
                knowledge_space=knowledge_space,
                actor_id=actor_id,
                actor_roles=actor_roles,
            )
        except (
            EmbeddingConfigurationError,
            VectorStoreConfigurationError,
            KnowledgeRetrievalError,
        ):
            trace.append(
                AgentTraceEvent(
                    agent_name="knowledge",
                    capability="authorized_hybrid_retrieval",
                    status="UNAVAILABLE",
                    summary=(
                        "knowledge evidence unavailable; policy remains authoritative"
                    ),
                )
            )
            return None
        trace.append(
            AgentTraceEvent(
                agent_name="knowledge",
                capability="authorized_hybrid_retrieval",
                status="SUCCEEDED",
                summary=f"returned {len(result.citations)} citations",
            )
        )
        return result

    def _safe_reply(
        self,
        *,
        message: str,
        facts: dict[str, object],
        fallback: str,
        trace: list[AgentTraceEvent],
    ) -> str:
        try:
            return self._supervisor.compose_reply(message=message, facts=facts)
        except AgentModelError:
            trace.append(
                AgentTraceEvent(
                    agent_name="supervisor",
                    capability="user_visible_summary",
                    status="FALLBACK",
                    summary="used deterministic reply after summary failure",
                )
            )
            return fallback

    @staticmethod
    def _citation_facts(
        knowledge: KnowledgeSearchResult | None,
    ) -> list[dict[str, object]]:
        if knowledge is None:
            return []
        return [
            {
                "title": citation.title,
                "version": citation.version_label,
                "source_uri": citation.source_uri,
                "excerpt": citation.excerpt,
            }
            for citation in knowledge.citations
        ]
