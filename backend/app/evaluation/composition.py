"""Production evaluation composition with no scenario or enterprise-write dependencies."""

from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.agents.llm import DeepSeekJsonModel
from app.agents.supervisor import SupervisorAgent
from app.core.config import Settings
from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.contracts import EvaluationRunMode
from app.evaluation.live import LiveReadOnlyCapabilities, LiveReadOnlyCaseExecutor
from app.evaluation.usage import EvaluationUsageGuard, EvaluationUsageLimits
from app.evaluation.worker import EvaluationWorker
from app.knowledge.embeddings import DashScopeEmbeddings
from app.knowledge.service import KnowledgeService
from app.knowledge.vector_store import ChromaKnowledgeVectorStore


def build_contract_evaluation_worker(
    sessions: sessionmaker[Session],
    *,
    repository_root: Path,
    worker_id: str,
) -> EvaluationWorker:
    return EvaluationWorker(
        sessions,
        EvaluationSuiteCatalog(repository_root),
        worker_id=worker_id,
    )


def build_live_read_only_evaluation_worker(
    sessions: sessionmaker[Session],
    settings: Settings,
    *,
    repository_root: Path,
    worker_id: str,
) -> EvaluationWorker:
    """Compose only Supervisor.plan and Knowledge.search with shared hard limits."""
    usage = EvaluationUsageGuard(
        EvaluationUsageLimits(
            max_calls=settings.evaluation_live_max_calls,
            max_input_tokens=settings.evaluation_live_max_input_tokens,
            max_output_tokens=settings.evaluation_live_max_output_tokens,
            max_embedding_texts=settings.evaluation_live_max_embedding_texts,
            max_run_duration=timedelta(
                seconds=settings.evaluation_live_run_timeout_seconds
            ),
        )
    )
    timeout = settings.evaluation_live_case_timeout_seconds
    planner = SupervisorAgent(
        DeepSeekJsonModel(
            (
                settings.deepseek_api_key.get_secret_value()
                if settings.deepseek_api_key
                else None
            ),
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            usage_observer=usage,
            request_timeout_seconds=timeout,
        )
    )
    knowledge = KnowledgeService(
        sessions,
        DashScopeEmbeddings(
            (
                settings.dashscope_api_key.get_secret_value()
                if settings.dashscope_api_key
                else None
            ),
            usage_observer=usage,
            request_timeout_seconds=timeout,
        ),
        ChromaKnowledgeVectorStore(settings.chroma_path),
    )
    return EvaluationWorker(
        sessions,
        EvaluationSuiteCatalog(repository_root),
        worker_id=worker_id,
        executor=LiveReadOnlyCaseExecutor(
            LiveReadOnlyCapabilities(
                planner=planner,
                knowledge=knowledge,
            ),
            usage,
        ),
        allowed_modes=(EvaluationRunMode.LIVE_READ_ONLY,),
        lease_duration=timedelta(
            seconds=max(30, settings.evaluation_live_case_timeout_seconds + 10)
        ),
    )
