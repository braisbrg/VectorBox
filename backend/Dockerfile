FROM python:3.11-slim

WORKDIR /app

# Security: Run as non-root user
RUN useradd -m -u 1000 vectorbox && \
    chown -R vectorbox:vectorbox /app

# Install system dependencies (Basic)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client && \
    rm -rf /var/lib/apt/lists/*


# Copy both: requirements.txt is the source of truth a human edits,
# requirements.lock is what actually gets installed.
COPY requirements.txt requirements.lock ./

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Install from the HASH-PINNED lock, not requirements.txt.
#
# The image used to build from requirements.txt, which carries `>=` pins and no
# transitive pins at all — so every rebuild re-resolved unverified, and CI's
# security gate was auditing requirements.lock, a file production never
# installed. (That drift was not hypothetical: the lock had sentence-transformers
# 3.4.1 while requirements.txt requires >=5.0.0.)
#
# --require-hashes makes a compromised or swapped release fail the build instead
# of shipping. It also removes the need for the separate CPU-torch line: the lock
# is compiled against the PyTorch CPU index, so it pins torch==2.10.0+cpu and
# resolves ZERO nvidia-* / triton packages (compiling it against PyPI instead
# pulls in ~15 CUDA packages — regenerate with the same --extra-index-url).
#
# Regenerate after editing requirements.txt:
#   docker compose exec backend python -m piptools compile --generate-hashes \
#     --allow-unsafe --extra-index-url https://download.pytorch.org/whl/cpu \
#     --output-file=requirements.lock requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --require-hashes \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.lock

# Copy application code
COPY . .

# Pre-download AI Model. `google/embeddinggemma-300m` is a gated HF model;
# the download step needs a HuggingFace token but we must NOT bake it into
# the image. Use a BuildKit secret mount so the value lives only for the
# duration of the RUN layer and never lands in `docker history`.
#
# Build with:
#   DOCKER_BUILDKIT=1 docker build --secret id=hf_token,env=HF_TOKEN ...
# or via compose: build.secrets: [hf_token]
ENV SENTENCE_TRANSFORMERS_HOME=/models_cache
# Download + fix ownership + purge credentials in ONE layer. Three reasons it
# must be a single RUN:
#   1. `chown -R` in its own layer duplicated the ENTIRE 1.2GB model cache —
#      a layer stores whole files, and a metadata-only change still rewrites
#      every one of them. The image carried the model twice (~1.27GB wasted).
#   2. download_model.py calls huggingface_hub.login() — REQUIRED for the gated
#      repo (env-only auth dies on the xet read-token refresh, GatedRepoError
#      401) — and login() persists the token to
#      /root/.cache/huggingface/{token,stored_tokens}. Deleting it in a LATER
#      layer would not help: the earlier layer still contains the file, and
#      `docker save` / a registry pull reads it straight out. The rm below is
#      LOAD-BEARING, not a nicety — it is the only thing keeping the token out
#      of the shipped image.
#   3. `--mount=type=secret` keeps the token out of `docker history`, but that
#      only covers the build ARG/ENV surface — anything the build WRITES TO DISK
#      is still shipped. Both halves are needed.
RUN --mount=type=secret,id=hf_token,mode=0444 \
    HF_TOKEN="$(cat /run/secrets/hf_token 2>/dev/null || true)" \
    python download_model.py && \
    rm -rf /root/.cache/huggingface /home/vectorbox/.cache/huggingface && \
    chown -R vectorbox:vectorbox /models_cache

# Switch to non-root user
USER vectorbox

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
