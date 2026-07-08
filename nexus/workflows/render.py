"""WorkflowSpec -> Mermaid flowchart — the conversational "preview".

Chat clients that render Mermaid (Claude Desktop, Obsidian, GitHub) show this as a real
flowchart; everywhere else it reads as a legible outline. Node shapes carry meaning:
stadium = trigger, diamond = condition, rectangle = action, rounded = terminal.
"""

from __future__ import annotations

import re

from nexus.workflows.blocks import BlockKind, get
from nexus.workflows.schema import StepSpec, WorkflowSpec


def _nid(step_id: str) -> str:
    """A Mermaid-safe node id."""
    return "s_" + re.sub(r"\W", "_", step_id)


def _esc(text: str) -> str:
    return text.replace('"', "'")


def _label(step: StepSpec) -> str:
    return _esc(step.label or step.id) + f"<br/><i>{_esc(step.block)}</i>"


def to_mermaid(spec: WorkflowSpec) -> str:
    """Render the workflow graph as a Mermaid flowchart definition."""
    lines = [
        "flowchart TD",
        f'    trigger(["{_esc(spec.trigger.block)}<br/>{_esc(_trigger_hint(spec))}"])',
    ]
    steps = {s.id: s for s in spec.steps}
    for step in spec.steps:
        block = get(step.block)
        if block is not None and block.kind == BlockKind.condition:
            lines.append(f'    {_nid(step.id)}{{"{_label(step)}"}}')
        else:
            lines.append(f'    {_nid(step.id)}["{_label(step)}"]')
    lines.append('    done(["end"])')

    if spec.entry in steps:
        lines.append(f"    trigger --> {_nid(spec.entry)}")
    for step in spec.steps:
        block = get(step.block)
        is_condition = block is not None and block.kind == BlockKind.condition
        ok_lbl, fail_lbl = ("yes", "no") if is_condition else ("ok", "on failure")
        ok_target = _nid(step.on_success) if step.on_success in steps else "done"
        lines.append(f"    {_nid(step.id)} -->|{ok_lbl}| {ok_target}")
        if step.on_failure in steps:
            lines.append(f"    {_nid(step.id)} -->|{fail_lbl}| {_nid(step.on_failure)}")
        elif is_condition:
            lines.append(f"    {_nid(step.id)} -->|{fail_lbl}| done")
    return "\n".join(lines)


def _trigger_hint(spec: WorkflowSpec) -> str:
    cfg = spec.trigger.config
    return " ".join(f"{k}={v}" for k, v in cfg.items()) or "manual"


def preview(spec: WorkflowSpec) -> str:
    """The full conversational preview: header + fenced Mermaid block."""
    return (
        f"**{spec.name}** (`{spec.slug}` v{spec.version}, {spec.status})\n"
        f"{spec.description}\n\n```mermaid\n{to_mermaid(spec)}\n```"
    )
