# Muneer's model server: vLLM plus the launcher in server/, for a RunPod GPU pod.
#
# Build and push from the repository root (the vLLM base image is ~8.7 GB):
#   docker build -f deploy/server.Dockerfile -t <dockerhub-user>/muneer-server:v1 .
#   docker push <dockerhub-user>/muneer-server:v1
# Then follow deploy/README.md, "C. Model on RunPod, app on a laptop".
#
# The image holds no weights and no secrets. The weights are downloaded from
# the private Hugging Face repo when the pod starts, using HF_TOKEN from the pod.
#
# The base is built on CUDA 13.0.2, so the host needs NVIDIA driver 580 or newer.
# On RunPod, filter GPUs to CUDA version 13.0 or higher.

# Pinned by digest: the launcher's vLLM flags were checked against this release.
FROM vllm/vllm-openai:v0.29.0@sha256:c2914767605584b6d8f45686b82de173ecc99e781897aa3d0a66dacd72c51ae1

# Caches live on the pod's persistent volume, so a restart doesn't download or
# compile again. HF_XET_HIGH_PERFORMANCE speeds up the ~30 GB download.
ENV HF_HOME=/workspace/.cache/huggingface \
    VLLM_CACHE_ROOT=/workspace/.cache/vllm \
    HF_XET_HIGH_PERFORMANCE=1 \
    MUNEER_DOWNLOAD_DIR=/workspace/models/muneer \
    MUNEER_HOST=0.0.0.0 \
    MUNEER_PORT=8000

COPY server/launch.py server/muneer_auth.py server/muneer_sampling.py server/chat_template.jinja \
    server/system_prompt.md /opt/muneer/server/

EXPOSE 8000

# /health is the one route that needs no API key (server/muneer_auth.py). The
# long start period covers the first download.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30m --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${MUNEER_PORT:-8000}/health" || exit 1

# Replaces the base image's ENTRYPOINT ["vllm", "serve"]: the launcher fetches
# the weights, then starts vllm serve itself with Muneer's settings.
ENTRYPOINT ["python3", "/opt/muneer/server/launch.py"]
