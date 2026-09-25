"""Top-level API router."""

from fastapi import APIRouter

from app.api.routes.access_requests import router as access_requests_router
from app.api.routes.agents import router as agents_router
from app.api.routes.approvals import router as approvals_router
from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.evaluations import router as evaluations_router
from app.api.routes.health import router as health_router
from app.api.routes.human_reviews import router as human_reviews_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.tickets import router as tickets_router
from app.api.routes.workflows import router as workflows_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(access_requests_router)
api_router.include_router(tickets_router)
api_router.include_router(workflows_router)
api_router.include_router(human_reviews_router)
api_router.include_router(approvals_router)
api_router.include_router(knowledge_router)
api_router.include_router(agents_router)
api_router.include_router(conversations_router)
api_router.include_router(evaluations_router)
