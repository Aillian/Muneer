# The Muneer app: the pipeline and the Gradio interface. It needs no GPU; it
# reaches the model server over its API. The same image also runs the mock
# model server (python -m mock_muneer) for testing without a GPU.
#
# Build from the repository root:
#   docker build -f deploy/app.Dockerfile -t muneer-app .
# Usually you won't build it by hand: docker compose does (see docker-compose.yml).

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    GRADIO_ANALYTICS_ENABLED=False \
    APP_HOST=0.0.0.0 \
    APP_PORT=7860 \
    DATABASE_PATH=/data/muneer.sqlite3

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them. The constraints
# pin every package to the version the test suite passed with.
COPY deploy/constraints.txt deploy/constraints.txt
COPY pipeline/requirements.txt pipeline/requirements.txt
COPY app/requirements.txt app/requirements.txt
COPY mock_muneer/requirements.txt mock_muneer/requirements.txt
RUN pip install -c deploy/constraints.txt -r app/requirements.txt -r mock_muneer/requirements.txt

COPY pipeline/ pipeline/
COPY app/ app/
COPY mock_muneer/ mock_muneer/

# Run as an unprivileged user; /data holds the database (a volume in compose).
RUN useradd --create-home --uid 10001 muneer \
    && mkdir -p /data \
    && chown muneer /data
USER muneer

EXPOSE 7860
# Gradio and uvicorn both stop cleanly on SIGINT (the app also handles SIGTERM).
STOPSIGNAL SIGINT
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('APP_PORT', '7860') + '/', timeout=4)"]

CMD ["python", "-m", "app"]
