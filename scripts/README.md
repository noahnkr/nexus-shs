# scripts/ — human-run CLIs

Operational, human-triggered commands (spec §9). Not part of the request path. Suggested
set (implement as needed):

| Script | Purpose |
|---|---|
| `authorize.py` | OAuth/device authorize a connector (writes tokens to the volume). |
| `subscribe.py` | Register a connector's webhook subscription with the vendor (uses `PUBLIC_URL`). |
| `sync.py` | Manually run a connector's deterministic poll-sync (§4.3) once. |
| `import.py` | Bulk-ingest a folder of source documents via `ingest.batch` (§3.7). |

Run inside the project venv, e.g. `python scripts/import.py ./docs-to-ingest`.
