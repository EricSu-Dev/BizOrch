FROM python:3.14.3-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY enterprise_system/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt


FROM python:3.14.3-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

ARG APP_UID=10002
ARG APP_GID=10002

RUN groupadd --system --gid "$APP_GID" enterprise \
    && useradd --system --uid "$APP_UID" --gid enterprise --home-dir /app --shell /usr/sbin/nologin enterprise

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=enterprise:enterprise enterprise_system/__init__.py ./enterprise_system/__init__.py
COPY --chown=enterprise:enterprise enterprise_system/app ./enterprise_system/app
COPY --chown=enterprise:enterprise enterprise_system/alembic.ini ./enterprise_system/alembic.ini
COPY --chown=enterprise:enterprise enterprise_system/migrations ./enterprise_system/migrations

USER enterprise

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=3).read()"]

CMD ["uvicorn", "enterprise_system.app.main:create_runtime_app", "--factory", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
