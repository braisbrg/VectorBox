FROM python:3.11-slim

WORKDIR /app

# Security: Run as non-root user
RUN useradd -m -u 1000 cinematch && \
    chown -R cinematch:cinematch /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Security: Audit dependencies
RUN pip-audit -r requirements.txt --strict


# Copy application code
COPY . .

# Switch to non-root user
USER cinematch

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
