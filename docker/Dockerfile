FROM python:3.12-slim

WORKDIR /app

# Install system dependencies in a single layer
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libpcsclite-dev \
    pcscd \
    pcsc-tools \
    opensc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cached layer - only rebuilds if requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY assets/ ./assets/

# Create necessary directories
RUN mkdir -p /app/uploads /app/temp

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]