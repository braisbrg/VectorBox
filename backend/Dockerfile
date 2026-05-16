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


# Security: Audit dependencies (allow errors for now as we are force-installing specialized wheels)
# RUN pip-audit -r requirements.txt --strict

# Copy requirements first for better caching
COPY requirements.txt .

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Optimize: 
# 1. Use BuildKit cache for pip
# 2. Install CPU-only Torch explicitly first (to avoid downloading CUDA version)
# 3. Install other requirements
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu && \
    pip install -r requirements.txt

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
RUN --mount=type=secret,id=hf_token,mode=0444 \
    HF_TOKEN="$(cat /run/secrets/hf_token 2>/dev/null || true)" \
    python download_model.py

# Switch to non-root user
# Ensure the user owns the model cache
RUN chown -R vectorbox:vectorbox /models_cache
USER vectorbox

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
