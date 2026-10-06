# Muneer all-in-one: the model server and the app in one container, so a
# single RunPod GPU pod serves the whole demo at one public address.
# Guide: deploy/README.md, "B. The all-in-one image (recommended)".
#
# Build and push from the repository root:
#   docker build -f deploy/all-in-one.Dockerfile -t <dockerhub-user>/muneer-all-in-one:v1 .
#   docker push <dockerhub-user>/muneer-all-in-one:v1
#
# deploy/all_in_one.py starts both processes. The app's port (7860) is served;
# vLLM listens on 127.0.0.1 inside the container, unless the pod sets
# MUNEER_API_KEY: then it also answers on port 8000, behind that key, for
# direct calls. The image holds no weights and no secrets: the weights are
# downloaded when the pod starts, using HF_TOKEN from the pod's settings.
#
# The base is built on CUDA 13.0.2, so the host needs NVIDIA driver 580 or newer.
# On RunPod, filter GPUs to CUDA version 13.0 or higher.

# The same pinned vLLM release as deploy/server.Dockerfile.
FROM vllm/vllm-openai:v0.29.0@sha256:c2914767605584b6d8f45686b82de173ecc99e781897aa3d0a66dacd72c51ae1

# Weights, caches and the database live on the pod's persistent volume at
# /workspace, so a restart doesn't download or compile again.
ENV HF_HOME=/workspace/.cache/huggingface \
    VLLM_CACHE_ROOT=/workspace/.cache/vllm \
    HF_XET_HIGH_PERFORMANCE=1 \
    MUNEER_DOWNLOAD_DIR=/workspace/models/muneer \
    APP_PORT=7860 \
    GRADIO_ANALYTICS_ENABLED=False \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/muneer

# The app gets its own Python and packages, apart from vLLM's, pinned to the
# versions its tests pass with (as in deploy/app.Dockerfile). The base
# image's uv override pins a CUDA library for vLLM; the app doesn't need it.
COPY deploy/constraints.txt deploy/constraints.txt
COPY pipeline/requirements.txt pipeline/requirements.txt
COPY app/requirements.txt app/requirements.txt
COPY mock_muneer/requirements.txt mock_muneer/requirements.txt
RUN UV_PYTHON_INSTALL_DIR=/opt/muneer/python uv venv --no-cache --python 3.14.7 /opt/muneer/venv \
    && env -u UV_OVERRIDE uv pip install --no-cache --python /opt/muneer/venv/bin/python \
        -c deploy/constraints.txt -r app/requirements.txt -r mock_muneer/requirements.txt

# The app runs as this unprivileged user; vLLM keeps root, as in the server image.
RUN useradd --create-home --uid 10001 muneer

COPY server/launch.py server/muneer_auth.py server/muneer_sampling.py server/chat_template.jinja \
    server/system_prompt.md server/
COPY pipeline/ pipeline/
COPY app/ app/
COPY mock_muneer/ mock_muneer/
COPY deploy/all_in_one.py deploy/all_in_one.py
# The defaults for every setting the pod doesn't set. It holds no secrets.
COPY .env.example .env.example

EXPOSE 7860

# The app's page answers once it is up. The long start period covers the
# first download of the weights.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30m --retries=3 \
    CMD curl -fsS -o /dev/null "http://127.0.0.1:${APP_PORT:-7860}/" || exit 1

# Replaces the base image's ENTRYPOINT ["vllm", "serve"].
ENTRYPOINT ["/opt/muneer/venv/bin/python", "/opt/muneer/deploy/all_in_one.py"]
