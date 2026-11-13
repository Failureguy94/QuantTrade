# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# --- dependencies -----------------------------------------------------------
# Built from pyproject.toml alone, so this layer is reused until the
# dependency list changes, however often the application code does.
FROM base AS deps
WORKDIR /build
COPY pyproject.toml .
RUN python -m venv /opt/venv \
 && python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > requirements.txt \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# --- runtime ----------------------------------------------------------------
FROM base AS runtime
# Unprivileged user with a home directory (yfinance keeps a small cache there).
RUN useradd --create-home --uid 10001 quant
COPY --from=deps /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /srv/quant
COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/start.sh ./start.sh
COPY app ./app

USER quant
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/api/v1/health', timeout=2)"

# One process: quotes, rate limits and the embedded worker live in its memory
# (ADR-011, ADR-016). Listens on $PORT, default 8000.
CMD ["./start.sh"]
