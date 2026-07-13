"""Schema-constrained classification.

The LLM emits frontmatter constrained by the schema's JSON schema (`json_schema_for`), so
it can ONLY produce valid fields and enum values — modeled as a forced tool call
whose input_schema IS the family's JSON schema. If your domain forbids certain data from
entering the vault (PHI, secrets), encode that as a classifier instruction here AND as a
schema rule — make the boundary structural.
"""

from __future__ import annotations

from typing import Any

from nexus.config import settings
from nexus.vault.schema import Family, json_schema_for

_MODEL = "claude-haiku-4-5-20251001"  # cheap; classification is structured, not creative

_SYSTEM = (
    "You classify a source document into a single vault note's frontmatter. Emit ONLY "
    "fields allowed by the provided tool schema. Write a tight one-line `summary`, choose "
    "accurate `tags`, and a descriptive `title`. Do not invent data not present in the "
    "document. Never copy medical details, SSNs, or financial account numbers verbatim "
    "into frontmatter — summarize at the level needed to find the note again."
)


def classify(text: str, *, hint_family: Family = Family.reference) -> dict[str, Any]:
    """Classify `text` into validated frontmatter for one note family.

    Uses Anthropic structured output: a forced tool call whose input_schema is
    json_schema_for(hint_family). Returns the raw frontmatter dict (the pipeline fills in
    family/status/created/updated/source_ref authoritatively afterwards).
    """
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    schema = json_schema_for(hint_family)
    msg = client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        system=_SYSTEM,
        tools=[
            {
                "name": "emit_note",
                "description": "Emit the note frontmatter.",
                "input_schema": schema,
            }
        ],
        tool_choice={"type": "tool", "name": "emit_note"},
        messages=[{"role": "user", "content": text[:20000]}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return dict(block.input)
    raise RuntimeError("classifier returned no tool_use block")
