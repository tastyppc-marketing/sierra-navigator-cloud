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
# Persistent dir for the SQLite audit/ledger db (mounted as a named volume in compose).
# Created appuser-owned so a fresh named volume inherits writable ownership on first mount.
RUN mkdir -p /home/appuser/app/var
EXPOSE 8080
# FastMCP Streamable-HTTP app (MCP at /mcp, health at /health). The entrypoint binds
# SIERRA_MCP_BIND_HOST (default 0.0.0.0) via sierra_mcp.server.main — the SAME value
# the no-auth loopback gate checks, so the bind can't diverge from the gate (#4/#13).
CMD ["python", "-m", "sierra_mcp.server"]
