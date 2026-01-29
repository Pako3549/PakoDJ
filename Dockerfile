# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory inside container
WORKDIR /app

# Install system dependencies required for the bot
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    unzip \
    ffmpeg \
    git \
    build-essential \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (JS runtime required by yt-dlp for YouTube)
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
      x86_64) deno_arch="x86_64-unknown-linux-gnu" ;; \
      aarch64|arm64) deno_arch="aarch64-unknown-linux-gnu" ;; \
      armv7l|armhf) deno_arch="armv7-unknown-linux-gnueabihf" ;; \
      *) echo "Unsupported arch: $arch"; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${deno_arch}.zip" -o /tmp/deno.zip; \
    unzip /tmp/deno.zip -d /usr/local/bin; \
    rm /tmp/deno.zip

# Ensure /usr/local/bin is on PATH for non-root user
ENV PATH="/usr/local/bin:${PATH}"

# Fail build early if deno is missing
RUN /usr/local/bin/deno --version

# Copy requirements file first to leverage Docker layer caching
COPY bot/requirements.txt .

# Install Python dependencies (prefer binary wheels, compile if needed)
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# Copy bot source code
COPY bot/ .

# Create a non-root user for security
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check to verify bot is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Command to run the bot
CMD ["python", "main.py"]
