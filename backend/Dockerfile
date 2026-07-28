FROM python:3.14.3-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt


FROM python:3.14.3-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

ARG APP_UID=10001
ARG APP_GID=10001

RUN groupadd --system --gid "$APP_GID" bizorch \
    && useradd --system --uid "$APP_UID" --gid bizorch --home-dir /app --shell /usr/sbin/nologin bizorch

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=bizorch:bizorch backend/app ./app
COPY --chown=bizorch:bizorch backend/migrations ./migrations
COPY --chown=bizorch:bizorch backend/alembic.ini ./alembic.ini
COPY --chown=bizorch:bizorch backend/start.py ./start.py
COPY --chown=bizorch:bizorch backend/seed_demo_users.py ./seed_demo_users.py
COPY --chown=bizorch:bizorch backend/run_knowledge_index_worker.py ./run_knowledge_index_worker.py
COPY --chown=bizorch:bizorch backend/run_evaluation_worker.py ./run_evaluation_worker.py
COPY --chown=bizorch:bizorch evaluations ./evaluations

RUN mkdir -p /app/data/chroma /app/data/checkpoints /app/data/uploads /app/logs \
    && chown -R bizorch:bizorch /app/data /app/logs

USER bizorch

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).read()"]

CMD ["python", "start.py"]
