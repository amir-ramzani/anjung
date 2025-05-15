# One image for api and worker (LLD 2, plan P0-03). Build from the repo root:
#   docker build -f backend/Dockerfile -t anjung-backend backend
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home anjung
WORKDIR /srv
COPY --from=build /srv/.venv /srv/.venv
COPY --from=build /srv/app /srv/app
ENV PATH="/srv/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=90s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"
# api by default; the worker service overrides the command
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--loop", "uvloop"]
