# Vault

Generated meta — do not hand-edit (spec §3.3). This root index rolls up the four families.
Regenerated from frontmatter by `nexus.vault.index.regenerate`.

- **reference/** — authored knowledge: SOPs, policy, pricing, voice, domain knowledge.
- **entity/** — current distilled state of each tracked thing (one note per entity).
- **events/** — append-only audit log (one note per day).
- **tasks/** — the human-approval queue.

> This is the seed vault baked into the image (§8). On first boot it seeds the volume once,
> then is never re-clobbered by deploys so live state survives.
