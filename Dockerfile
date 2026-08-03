FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

WORKDIR /app
COPY --from=builder /install /usr/local
COPY jobs_agent/ jobs_agent/

ENV PYTHONUNBUFFERED=1 \
    JOBS_DB=/app/data/jobs.db

# Default: one detect-classify-log sweep. For the MCP server, override with:
#   docker run -i ai-jobs-agent python -m jobs_agent.mcp_server
CMD ["python", "-m", "jobs_agent.cli", "sweep"]
