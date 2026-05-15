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

# Pre-download AI Model (HF_TOKEN needed for gated google/embeddinggemma-300m)
ARG HF_TOKEN
ENV SENTENCE_TRANSFORMERS_HOME=/models_cache
ENV HF_TOKEN=${HF_TOKEN}
RUN python download_model.py

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
