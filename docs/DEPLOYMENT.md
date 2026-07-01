# Deploying Nexus

Nexus is deliberately **one service binding one volume** (no broker, no vector-DB cluster,
in-process search). That makes it trivial to host on any platform that gives you a
container + a persistent disk. This guide covers **Railway** (recommended), then generic
Docker and other PaaS.

The whole runtime contract:

| Need | How Nexus expects it |
|---|---|
| Process | one web service running `uvicorn nexus.app:app` |
| Disk | one persistent volume mounted at `VAULT_PATH` (default `/data/vault`) |
| Health | `GET /health` → `200 {"status":"ok"}` |
| Inbound events | `POST /webhooks/{source}` (per-source HMAC) |
| Clock | something POSTing `POST /cron/{job}` with the `CRON_TOKEN` bearer |
| Chat/MCP | `/mcp` (Streamable HTTP) for a desktop MCP client |

## The seed-once vault model (important)

The repo's `vault/` is a **seed** baked into the image. On first boot the container
entrypoint (`docker-entrypoint.sh`) copies the seed into the mounted volume **once** (it
only seeds when `VAULT_PATH/INDEX.md` is absent). After that, deploys never clobber live
state — entities, events, tasks, and edits accumulate on the volume and survive restarts
and redeploys.

Consequence: **your volume is the source of truth in production.** Back it up. Editing the
seed in the repo only affects *fresh* environments, not existing ones. To pull live
reference/context changes back for git review, export them from the volume yourself (state
layers never leave the box automatically — spec §8).

## Environment variables

See [`.env.example`](../.env.example) for the full list. Minimum for production:

| Var | Required | Notes |
|---|---|---|
| `NEXUS_ENV` | yes | set to `prod` — the config validator then **fails fast** if secrets are missing/default |
| `VAULT_PATH` | yes | absolute path on the mounted volume, e.g. `/data/vault` |
| `PUBLIC_URL` | yes | your public https URL; used to build webhook callback URLs |
| `MCP_TOKEN` | yes | bearer for the MCP surface |
| `CRON_TOKEN` | yes | bearer for `POST /cron/{job}` |
| `ANTHROPIC_API_KEY` | yes | powers the agent loop + ingest classifier |
| `EMBEDDING_API_KEY` | no | Voyage key; **omit to run BM25-only** (semantic dormant) |
| `<SOURCE>_WEBHOOK_SECRET` | per connector | HMAC secret for each `connectors/<source>/` (e.g. `EXAMPLE_WEBHOOK_SECRET`) |
| `OWNER_CONTACT` | no | where owner notifications go (matches your `notify` transport) |

> In `prod`, startup is blocked if `MCP_TOKEN`, `CRON_TOKEN`, or `ANTHROPIC_API_KEY` are
> missing or still `changeme-…`. This is intentional (`nexus/config.py`).

---

## Railway (recommended)

Railway gives you a container + a volume + a cron trigger, which is exactly Nexus's shape.

1. **Create the service.** New Project → Deploy from your repo. Railway detects the
   `Dockerfile` and builds it.
2. **Add a volume.** Service → Variables/Volumes → **Add Volume**, mount path `/data`.
   (The Dockerfile declares `VOLUME ["/data"]` and defaults `VAULT_PATH=/data/vault`.)
3. **Set variables** (Service → Variables): everything in the table above. Set
   `PUBLIC_URL` to the Railway-provided domain, `NEXUS_ENV=prod`.
4. **Expose the port.** Railway sets `$PORT`; the entrypoint binds it automatically. Add a
   public domain under Settings → Networking.
5. **Health check.** Settings → Deploy → Health Check Path = `/health`.
6. **Cron.** Add a **Cron Schedule** (or a second tiny "cron" service) that runs on your
   cadence and calls the app over HTTP — Nexus triggers cron via HTTP, no second process
   needs the volume (spec §5.6):
   ```bash
   curl -fsS -X POST "$PUBLIC_URL/cron/daily-digest" \
     -H "Authorization: Bearer $CRON_TOKEN"
   ```
   One cron entry per job (`daily-digest`, `vault-health`, any deterministic sync you
   registered in `connectors/ingress/jobs.py`).
7. **Point your connectors' webhooks** at `"$PUBLIC_URL"/webhooks/<source>` and configure
   each vendor with the matching `<SOURCE>_WEBHOOK_SECRET`.

---

## Generic Docker (any host: Fly.io, Render, a VPS, ECS…)

```bash
docker build -t nexus .
docker run -d --name nexus \
  -p 8000:8000 \
  -v nexus_data:/data \
  -e NEXUS_ENV=prod \
  -e PUBLIC_URL=https://nexus.example.com \
  -e MCP_TOKEN=... -e CRON_TOKEN=... -e ANTHROPIC_API_KEY=... \
  -e EXAMPLE_WEBHOOK_SECRET=... \
  nexus
curl -fsS localhost:8000/health
```

- **Fly.io:** `fly launch` (uses the Dockerfile), then `fly volumes create nexus_data`
  and mount it at `/data`; set secrets with `fly secrets set`. Trigger cron with
  `fly machine run` on a schedule, or an external scheduler hitting `/cron/{job}`.
- **Render:** a Web Service from the Dockerfile + a Disk mounted at `/data` + a Cron Job
  service that curls `/cron/{job}`.
- **Bare VPS:** the `docker run` above behind a TLS reverse proxy (Caddy/nginx) plus a
  `crontab` line per job.

## Running without Docker

The container is just convenience. Anything that runs the ASGI app works:

```bash
pip install .
VAULT_PATH=/srv/nexus/vault NEXUS_ENV=prod uvicorn nexus.app:app --host 0.0.0.0 --port 8000
```
Seed the volume yourself the first time (`cp -a vault/. /srv/nexus/vault/`).

## Securing the surface

- **Webhooks** self-authenticate (per-source HMAC, constant-time; replay window; 503 if no
  secret configured). Safe to expose publicly.
- **Cron** requires the `CRON_TOKEN` bearer. Keep the token secret; rotate on leak.
- **MCP (`/mcp`)** is your privileged control plane. Prefer to keep it private (VPN /
  Tailscale / IP allowlist) or put it behind your platform's auth. Treat `MCP_TOKEN` as
  a high-value secret. The conversational agent can also run purely as a *local* desktop
  MCP client against a private deployment.

## Scaling notes (when, not now)

Search is in-process and the vault rebuilds sub-second at small scale. Run **one replica**
— multiple replicas would each hold their own in-memory index and race on the volume. Reach
for a broker, a shared vector store, or horizontal scale only when volume genuinely forces
it (spec §1.10). Until then, a bigger box beats more boxes.
