"""Natural language -> WorkflowSpec — the conversational build loop.

The owner describes trigger + steps in prose; one schema-constrained Messages call
compiles it against the block catalog. Same trick as the ingest classifier (§3.2 #1): the
LLM is forced through a tool whose input schema IS `WorkflowDraft.model_json_schema()`, so
it can only emit a structurally valid draft; graph/block problems it can still make are
validated deterministically and fed back for one retry — which is what makes the process
consistent and repeatable rather than vibes.

Iteration model (draft -> preview -> revise -> activate):
  create_workflow  compile from scratch, save as status:draft, return Mermaid preview
  revise_workflow  recompile the SAVED spec + a change instruction, bump version,
                   drop back to draft (an edited workflow must be re-approved)
Activation is a separate explicit step (`store.set_workflow_status(slug, "active")`).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from nexus.config import settings
from nexus.vault import io
from nexus.workflows import store
from nexus.workflows.blocks import catalog
from nexus.workflows.schema import WorkflowDraft, WorkflowSpec, utcnow, validate_graph

MODEL = "claude-sonnet-5"
_MAX_COMPILE_ATTEMPTS = 3

_SYSTEM = """You compile natural-language workflow descriptions into a workflow draft.
Rules:
- Use ONLY blocks from the catalog below; never invent block names or config keys.
- Exactly one trigger. Steps form a graph via on_success/on_failure step ids.
- Conditions branch: on_success = true path, on_failure = false path.
- Anything that would reach an OUTSIDE party (send an email/text/post) MUST be a
  vault.create_task step (a draft for owner approval). There is no send capability.
- Use agent.run only where judgment/drafting/research is needed; use deterministic
  blocks for plumbing. Config values may reference context as {{trigger.payload.x}}
  or {{steps.<id>.output}}.
- Keep slugs kebab-case and step ids short and meaningful.

# Block catalog
"""


def _compile_tool() -> dict:
    return {
        "name": "emit_workflow",
        "description": "Emit the compiled workflow draft.",
        "input_schema": WorkflowDraft.model_json_schema(),
    }


def _unwrap_emission(raw: Any) -> Any:
    """Peel a spurious single wrapper key off the model's emission.

    Despite the flat `WorkflowDraft` tool schema, the compile model occasionally nests the
    draft under one wrapper key (e.g. ``{"workflow": {...}}`` / ``{"draft": {...}}``). Strip
    exactly one such layer so validation sees the draft it expects.
    """
    if isinstance(raw, dict) and len(raw) == 1:
        inner = next(iter(raw.values()))
        if isinstance(inner, dict) and "steps" in inner:
            return inner
    return raw


def _validate_emission(raw: Any) -> tuple[WorkflowDraft | None, list[str]]:
    """Turn a raw tool emission into a validated draft plus any deterministic problems.

    Schema mismatches (returned as problems, not raised) and graph/block errors both feed
    the same retry loop, so a malformed emission is corrected rather than surfaced.
    """
    from nexus.workflows.blocks import get

    try:
        draft = WorkflowDraft.model_validate(_unwrap_emission(raw))
    except ValidationError as e:
        return None, [f"draft did not match the schema: {e.errors(include_url=False)}"]

    problems = validate_graph(draft)
    problems += [f"unknown block '{s.block}'" for s in draft.steps if get(s.block) is None]
    if get(draft.trigger.block) is None:
        problems.append(f"unknown trigger block '{draft.trigger.block}'")
    return draft, problems


async def compile_draft(request: str, existing: WorkflowSpec | None = None) -> WorkflowDraft:
    """One compile pass: NL request (+ optionally the current spec) -> validated draft.

    Deterministic validation (graph shape + block existence) runs after each attempt;
    problems are fed back to the model, up to _MAX_COMPILE_ATTEMPTS.
    """
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    system = _SYSTEM + json.dumps(catalog(), indent=1)
    user = request
    if existing is not None:
        user = (
            "Current workflow definition:\n"
            + existing.model_dump_json(indent=1)
            + "\n\nRevision instruction:\n"
            + request
            + "\nKeep the same slug. Change only what the instruction requires."
        )
    messages: list[dict] = [{"role": "user", "content": user}]

    last_problems: list[str] = []
    for _ in range(_MAX_COMPILE_ATTEMPTS):
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=[_compile_tool()],
            tool_choice={"type": "tool", "name": "emit_workflow"},
            messages=messages,
        )
        block = next(b for b in resp.content if b.type == "tool_use")
        draft, problems = _validate_emission(block.input)
        if draft is not None and not problems:
            return draft

        last_problems = problems
        messages.append({"role": "assistant", "content": resp.content})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Invalid — fix and re-emit: {problems}",
                        "is_error": True,
                    }
                ],
            }
        )
    raise ValueError(f"could not compile a valid workflow: {last_problems}")


async def create_workflow(request: str) -> WorkflowSpec:
    """Compile a brand-new workflow from prose and save it as a draft."""
    draft = await compile_draft(request)
    slug = io.slugify(draft.slug)
    if store.load_workflow(slug) is not None:
        raise ValueError(f"workflow '{slug}' already exists — use revise_workflow")
    spec = WorkflowSpec(
        **{**draft.model_dump(), "slug": slug}, created=utcnow(), updated=utcnow()
    )
    store.save_workflow(spec)
    return spec


async def revise_workflow(slug: str, instruction: str) -> WorkflowSpec:
    """Recompile a saved workflow with a change instruction. Bumps version; the result
    returns to `draft` regardless of prior status — edits must be re-approved."""
    existing = store.load_workflow(slug)
    if existing is None:
        raise FileNotFoundError(f"no workflow '{slug}'")
    draft = await compile_draft(instruction, existing=existing)
    spec = WorkflowSpec(
        **{**draft.model_dump(), "slug": existing.slug},
        version=existing.version + 1,
        created=existing.created,
        updated=utcnow(),
    )
    store.save_workflow(spec)
    return spec
