FROM python:3.12-slim

# No BuildKit-only syntax here on purpose: the Cloud Build docker builder does not enable
# BuildKit by default, so cache mounts fail the build rather than degrading to no cache.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# git is a runtime dependency, not a build one: the scan fetcher shells out to it to clone a
# tenant's repository. Without it the worker starts cleanly and every scan fails at the clone
# with FileNotFoundError, which reads as a bug in our code rather than a missing package.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY migrations/ migrations/
COPY alembic.ini ./

RUN uv sync --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

# Non-root. The analysis workers process untrusted content, so the container should not be
# able to write outside its own working set even before egress rules apply.
RUN useradd --create-home --uid 10001 bugmine && chown -R bugmine:bugmine /app
USER bugmine

EXPOSE 8080
CMD ["uvicorn", "--factory", "bugmine.api:create_app", "--host", "0.0.0.0", "--port", "8080"]
