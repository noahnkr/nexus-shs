# Nexus — one service, one volume (spec §1.10 / §8).
# The image bakes the package + the SEED vault. On first boot the entrypoint copies the
# seed into the mounted volume once, then never clobbers it, so live state survives deploys.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first (better layer caching), then the package.
COPY pyproject.toml README.md ./
COPY nexus ./nexus
RUN pip install .

# Bake the seed vault (reference + context + framework files) into the image.
COPY vault ./vault
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The live vault lives on a mounted volume, NOT in the image layer. The volume is attached
# by the platform at /data (Railway: Service → Volumes; Docker: `-v nexus_data:/data`). We
# deliberately do NOT declare `VOLUME ["/data"]` here — Railway rejects the Dockerfile VOLUME
# instruction ("docker VOLUME is not supported"), and the directive is only a hint that the
# runtime mount does not need.
ENV VAULT_PATH=/data/vault \
    NEXUS_ENV=prod

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
