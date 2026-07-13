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
layers never leave the box automatically).

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
The repo ships a [`railway.json`](../railway.json) that pins the Dockerfile builder, the
`/health` check, an on-failure restart policy, and a single replica — so most of the steps
below are already configured on first deploy.

> **Do not declare a volume in the Dockerfile.** Railway rejects the `VOLUME` instruction
> ("docker VOLUME is not supported") — the persistent disk is attached from the service
> settings (step 2), not baked into the image. The Dockerfile intentionally omits it.

1. **Create the service.** New Project → Deploy from your repo. Railway reads `railway.json`
   and builds the `Dockerfile`.
2. **Add a volume.** Service → **Volumes** → **Add Volume**, mount path `/data`. This is the
   only source of production state; the image defaults `VAULT_PATH=/data/vault`, and the
   entrypoint seeds it once on first boot.
3. **Set variables** (Service → Variables): everything in the table above. Set
   `PUBLIC_URL` to the Railway-provided domain, `NEXUS_ENV=prod`.
4. **Expose the port.** Railway sets `$PORT`; the entrypoint binds it automatically. Add a
   public domain under Settings → Networking.
5. **Health check.** Already set to `/health` via `railway.json` (override under
   Settings → Deploy if needed).
6. **Cron.** Add a **Cron Schedule** (or a second tiny "cron" service) that runs on your
   cadence and calls the app over HTTP — Nexus triggers cron via HTTP, no second process
   needs the volume:
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

## Connecting Claude to `/mcp` (and the Railway 421 saga)

Two very different client paths exist — know which one you're debugging:

1. **claude.ai / Claude Desktop "custom connectors"** — the connection originates from
   **Anthropic's servers**, not your machine. Local success (MCP Inspector, curl,
   localhost) proves nothing about this path.
2. **`claude_desktop_config.json` with `mcp-remote`** — a local stdio proxy on your
   machine talks to your server over plain HTTP/1.1. This is the path that supports the
   static `MCP_TOKEN` bearer, and the one we recommend for a private Nexus:

   ```json
   {
     "mcpServers": {
       "nexus": {
         "command": "npx",
         "args": [
           "mcp-remote", "https://<your-app>.up.railway.app/mcp",
           "--header", "Authorization: Bearer <MCP_TOKEN>"
         ]
       }
     }
   }
   ```

   Custom connectors (path 1) cannot attach a static bearer — they only speak OAuth — so
   against this server's `StaticTokenVerifier` they will always stop at 401 even once
   transport issues are resolved. Use `mcp-remote`, or put a proper OAuth provider in
   front if you need path 1.

### `421 Misdirected Request` checklist

Railway's edge (hikari) is strict; three distinct causes all present as 421:

- **Chunked SSE responses.** The edge rejects `text/event-stream` chunked replies. Fixed
  in code: the MCP app runs `json_response=True, stateless_http=True` (buffered JSON,
  no server-initiated stream).
- **The `/mcp` → `/mcp/` redirect.** Clients are configured with the no-slash URL; the
  307 bounce back through the edge (with an `http://` Location when proxy headers aren't
  trusted) misdirects. Fixed in code: `MountSlashMiddleware` serves the exact path, and
  the entrypoint passes `--proxy-headers --forwarded-allow-ips '*'` so uvicorn builds
  `https://` URLs behind the proxy. Verify with:
  `curl -si -X POST https://<app>/mcp -H 'accept: application/json, text/event-stream' -H 'content-type: application/json' -d '{}'`
  — you should see a JSON response (401 without the bearer), **never** a 307.
- **HTTP/2 connection coalescing on `*.up.railway.app`.** All Railway apps share edge
  IPs and a wildcard cert, so a client that pools HTTP/2 connections (Anthropic's
  connector fetcher does) can reuse a connection whose TLS SNI names a *different*
  Railway app — the edge answers 421 by design. Not fixable in app code. Fix by
  attaching a **custom domain** to the service (its own cert defeats coalescing), or by
  using the `mcp-remote` path above (HTTP/1.1, dedicated connection).

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
it. Until then, a bigger box beats more boxes.
