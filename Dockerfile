FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=pyrunner.settings

WORKDIR /app

# Install system dependencies + Node.js 20 + the Claude Code CLI.
# The Claude Agent SDK (claude-agent-sdk) drives the `claude` Node CLI under the
# hood, so Node and the CLI must be available on PATH for the AI integration.
ENV NODE_MAJOR=20
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    bubblewrap \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    # postgres client (pg_dump/psql) for Databases backup/restore — PGDG, not
    # Debian's, so the client is never older than the data server it dumps
    # (pg_dump refuses servers newer than itself; v17 covers 9.2→17).
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /etc/apt/keyrings/pgdg.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs postgresql-client-17 \
    && npm install -g @anthropic-ai/claude-code \
    && claude --version \
    && pg_dump --version \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /app/data/environments /app/data/workdir

# Collect static files (build-time only keys, not used at runtime)
ENV SECRET_KEY="build-only-key-not-for-runtime"
ENV ENCRYPTION_KEY="QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="
RUN python manage.py collectstatic --noinput

# Copy and set up entrypoint (convert Windows CRLF to Unix LF)
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Expose port
EXPOSE 8000

# Healthcheck (mirrors docker-compose.yml so `docker run` users get it too).
# start-period covers first-boot migrations + plugin preflight before gunicorn
# is reachable. PORT is pinned to 8000 inside the container.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')"

# Set entrypoint
ENTRYPOINT ["/entrypoint.sh"]
