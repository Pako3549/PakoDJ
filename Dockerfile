###############################################################################
# Stage 1 – Builder: install build tools, deps, Deno, Python packages
###############################################################################
FROM python:3.11-slim AS builder

WORKDIR /build

# Build-time system deps (these stay out of the final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    unzip \
    git \
    build-essential \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (JS runtime required by yt-dlp for YouTube)
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
      x86_64)        deno_arch="x86_64-unknown-linux-gnu" ;; \
      aarch64|arm64) deno_arch="aarch64-unknown-linux-gnu" ;; \
      *) echo "Unsupported arch for Deno: $arch"; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${deno_arch}.zip" \
         -o /tmp/deno.zip; \
    unzip /tmp/deno.zip -d /usr/local/bin; \
    chmod +x /usr/local/bin/deno; \
    rm /tmp/deno.zip

# Fail build early if deno is missing
RUN /usr/local/bin/deno --version

# Install Python deps into a virtual-env so we can copy them cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY bot/requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# Verify davey installed correctly (catches missing ARM wheels early)
RUN python -c "import davey; print(f'davey {davey.__version__} OK')"

###############################################################################
# Stage 2 – Runtime: only what the bot needs to run
###############################################################################
FROM python:3.11-slim

WORKDIR /app

# Runtime-only system deps (no compilers, no git, no curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    libopus0 \
    libsodium23 \
    libffi8 \
    && rm -rf /var/lib/apt/lists/*

# Copy Deno binary from builder
COPY --from=builder /usr/local/bin/deno /usr/local/bin/deno

# Copy the pre-built Python virtual-env
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:/usr/local/bin:${PATH}"

# Quick sanity checks
RUN deno --version \
    && python -c "import davey, discord, yt_dlp, nacl; print('all imports OK')"

# Copy bot source code
COPY bot/ .

# Non-root user for security
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Run the bot
CMD ["python", "main.py"]
