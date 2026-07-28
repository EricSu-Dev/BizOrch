"""Lightweight process health endpoint."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    """Public, non-sensitive health information."""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Report that the API process can receive requests."""
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )

