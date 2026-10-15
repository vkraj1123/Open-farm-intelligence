#!/bin/sh
# Temporary Render staging entrypoint. Run ordered, checksum-verified schema
# migrations before accepting traffic; the runner serializes concurrent attempts.
set -eu

if [ -z "${OFI_DATABASE_URL:-}" ]; then
  echo "OFI_DATABASE_URL is required; refusing to start without durable storage." >&2
  exit 1
fi

ofi-schema
exec uvicorn ofi.api:app --host 0.0.0.0 --port "${PORT:-10000}"
