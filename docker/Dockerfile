FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (minimal - no OCR libraries)
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libpcsclite-dev \
    pcscd \
    pcsc-tools \
    opensc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
# Copy assets (signature stamp image) so visual signature is applied in container
COPY assets/ ./assets/

# Create necessary directories
RUN mkdir -p /app/uploads /app/temp

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Render sets PORT at runtime; default 8000 for local
ENV PORT=8000
EXPOSE 8000

# Run application (use PORT so Render can inject its port)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
