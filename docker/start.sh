#!/bin/sh
# Container entry point for the API.
#
# RUN_MIGRATIONS=true applies migrations and loads reference data before serving.
# Use it where the platform has no separate release step (both commands are safe to
# repeat). The compose stack leaves it off and runs them in its own one-shot service.
set -e

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  alembic upgrade head
  python -m app.cli seed
fi

# PORT is set by most hosting platforms. Behind their proxy, set FORWARDED_ALLOW_IPS
# (uvicorn reads it) so the client address used for rate limits is the real one.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
