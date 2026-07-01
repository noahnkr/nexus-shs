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

# The live vault lives on a mounted volume, NOT in the image layer.
ENV VAULT_PATH=/data/vault \
    NEXUS_ENV=prod
VOLUME ["/data"]

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
