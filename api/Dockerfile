# Multi-stage build for FastAPI backend
FROM python:3.11-slim as base

# Set working directory
WORKDIR /app

# Install system dependencies (includes Chromium deps for Playwright)
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libxkbcommon0 \
    libasound2t64 \
    libatspi2.0-0 \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser binary (system deps already installed above)
RUN playwright install chromium

# Copy application code
COPY . .

# Create non-root user and transfer Playwright browser cache
RUN useradd -m -u 1000 apiuser && chown -R apiuser:apiuser /app \
    && mkdir -p /home/apiuser/.cache \
    && cp -r /root/.cache/ms-playwright /home/apiuser/.cache/ \
    && chown -R apiuser:apiuser /home/apiuser/.cache
USER apiuser

# Expose port
EXPOSE 4174

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:4174/api/status')" || exit 1

# Start uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "4174"]
