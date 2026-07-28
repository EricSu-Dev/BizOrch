"""Knowledge Agent restricted to the enterprise retrieval capability."""

from app.knowledge.contracts import KnowledgeSearchResult
from app.knowledge.service import KnowledgeService


class KnowledgeAgent:
    def __init__(self, knowledge: KnowledgeService) -> None:
        self._knowledge = knowledge

    def search(
        self,
        *,
        query: str,
        knowledge_space: str,
        actor_id: str,
        actor_roles: frozenset[str],
    ) -> KnowledgeSearchResult:
        return self._knowledge.search(
            query=query,
            knowledge_space=knowledge_space,
            actor_id=actor_id,
            actor_roles=actor_roles,
            top_k=5,
        )
