FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install minimal system dependencies required for building wheels / TgCrypto
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first so Docker caches installed packages across builds
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install uvloop

# Copy the rest of the repository
COPY . .

# Fix Windows line endings (CRLF) if any and make start.sh executable
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

EXPOSE 5010

CMD ["bash", "start.sh"]
