"""Typed settings (spec §8: pydantic-settings, prod fail-fast).

One `Settings` object is the single source of configuration. In `prod` the validator
refuses to start if a required secret is missing — never trust blindly (§5.3 in spirit).
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", extra="ignore", case_sensitive=False
    )

    # --- core ---
    nexus_env: str = "dev"  # "dev" | "prod"
    vault_path: Path = Path("./vault")
    public_url: str = "http://localhost:8000"

    # --- auth ---
    mcp_token: str = "changeme-mcp-bearer"
    cron_token: str = "changeme-cron-bearer"

    # --- models ---
    anthropic_api_key: str = ""
    embedding_api_key: str | None = None  # None => semantic search dormant (§3.4)

    # --- per-source connector secrets (one per connectors/<source>/) ---
    example_webhook_secret: str | None = None
    welcomehome_api_key: str | None = None  # Exports API token (docs/connectors/welcomehome.md)

    # --- owner notifications (§4.4) ---
    owner_contact: str | None = None

    @property
    def is_prod(self) -> bool:
        return self.nexus_env.lower() == "prod"

    @property
    def semantic_enabled(self) -> bool:
        """Spec §3.4: semantic is optional and dormant until a key is set."""
        return bool(self.embedding_api_key)

    @model_validator(mode="after")
    def _fail_fast_in_prod(self) -> Self:
        if not self.is_prod:
            return self
        required = {
            "MCP_TOKEN": self.mcp_token,
            "CRON_TOKEN": self.cron_token,
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
        }
        missing = [k for k, v in required.items() if not v or v.startswith("changeme")]
        if missing:
            raise ValueError(f"prod startup blocked — missing/default secrets: {missing}")
        return self


settings = Settings()
