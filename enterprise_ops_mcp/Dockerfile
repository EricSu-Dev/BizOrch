FROM python:3.14.3-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY enterprise_ops_mcp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt


FROM python:3.14.3-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

ARG APP_UID=10003
ARG APP_GID=10003

RUN groupadd --system --gid "$APP_GID" mcp \
    && useradd --system --uid "$APP_UID" --gid mcp --home-dir /app --shell /usr/sbin/nologin mcp

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=mcp:mcp backend/app ./app
COPY --chown=mcp:mcp enterprise_ops_mcp/__init__.py ./enterprise_ops_mcp/__init__.py
COPY --chown=mcp:mcp enterprise_ops_mcp/app ./enterprise_ops_mcp/app

USER mcp

EXPOSE 8200

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import socket; s=socket.create_connection(('127.0.0.1',8200),3); s.close()"]

CMD ["python", "-m", "enterprise_ops_mcp.app.server"]
