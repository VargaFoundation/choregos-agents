# SPDX-License-Identifier: Apache-2.0
# The ServiceNow MCP server. Build context: plugins/servicenow/server.
# CI builds, signs (cosign) and publishes it; servers/servicenow.yaml pins it by digest.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PORT=8080
COPY --from=build /app/.venv /app/.venv
# A numeric non-root user, so the platform's `runAsNonRoot` can check it without a passwd entry.
USER 65532:65532
EXPOSE 8080
# Exec form: nothing runs through a shell.
ENTRYPOINT ["/app/.venv/bin/choregos-servicenow"]
