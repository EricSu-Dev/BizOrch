"""FastAPI application factory for BizOrch."""

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.runtime import ApplicationRuntime, build_runtime


def create_app(
    *,
    settings: Settings | None = None,
    runtime_factory: Callable[[Settings], ApplicationRuntime] = build_runtime,
) -> FastAPI:
    """Create the ASGI app; long-lived resources open only during lifespan."""
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime: ApplicationRuntime | None = None
        if resolved_settings.database_url.strip():
            runtime = runtime_factory(resolved_settings)
            application.state.runtime = runtime
        try:
            yield
        finally:
            if runtime is not None:
                runtime.close()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.debug,
        description="跨行业企业智能服务与业务流程自动化平台",
        lifespan=lifespan,
    )
    register_exception_handlers(application)
    application.include_router(api_router, prefix=resolved_settings.api_prefix)
    return application


app = create_app()
