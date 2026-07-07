#!/bin/sh
# Seed the volume ONCE, then hand off to the server (spec §8: seed baked into the image;
# the volume is seeded on first boot and never re-clobbered, so live state survives deploys).
set -e

: "${VAULT_PATH:=/data/vault}"
: "${PORT:=8000}"

# First boot only: the volume is empty until INDEX.md exists. Copy the baked seed in.
if [ ! -f "$VAULT_PATH/INDEX.md" ]; then
  echo "nexus: seeding fresh vault at $VAULT_PATH"
  mkdir -p "$VAULT_PATH"
  cp -a /app/vault/. "$VAULT_PATH/"
else
  echo "nexus: existing vault found at $VAULT_PATH — leaving live state intact"
fi

# Trust the platform proxy's X-Forwarded-* headers. Railway (and any PaaS edge) terminates
# TLS and connects from a non-loopback address; without --forwarded-allow-ips uvicorn
# ignores the headers and the ASGI scope says scheme=http — so every absolute URL the app
# builds (redirect Locations, MCP auth resource metadata) comes out http:// and breaks
# clients that follow it back through the edge.
exec uvicorn nexus.app:app --host 0.0.0.0 --port "$PORT" \
  --proxy-headers --forwarded-allow-ips '*'
