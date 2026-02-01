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

# Create necessary directories
RUN mkdir -p /app/uploads /app/temp

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
