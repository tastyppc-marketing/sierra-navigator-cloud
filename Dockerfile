# syntax=docker/dockerfile:1
# Tiny browserless image: httpx + fastmcp only, no Chromium.
FROM python:3.13-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home appuser
USER appuser
WORKDIR /home/appuser/app
COPY --chown=appuser:appuser . .
EXPOSE 8080
# FastMCP Streamable-HTTP app (MCP at /mcp/, health at /health).
CMD ["uvicorn", "sierra_mcp.server:app", "--host", "0.0.0.0", "--port", "8080"]
