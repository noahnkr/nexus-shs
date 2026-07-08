"""Acceptance tests for the workflows layer (scaffold).

Everything here is keyless — the engine tests exercise deterministic blocks only. The
builder (LLM compile) test runs only when ANTHROPIC_API_KEY is set, matching the agent
loop test's convention.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest


@pytest.fixture(autouse=True)
def vault(tmp_path, monkeypatch):
    """Isolate each test on a fresh temp vault (same convention as test_acceptance)."""
    from nexus.config import settings
    from nexus.vault import search

    monkeypatch.setattr(settings, "vault_path", tmp_path)
    monkeypatch.setattr(search, "_index", None)
    for fam in ("reference", "entity", "events", "tasks"):
        (tmp_path / fam).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _spec(**overrides):
    """A small deterministic workflow: condition -> log or task."""
    from nexus.workflows.schema import StepSpec, TriggerSpec, WorkflowSpec, utcnow

    base = dict(
        slug="test-flow",
        name="Test Flow",
        description="branch on payload type",
        trigger=TriggerSpec(block="trigger.webhook", config={"source": "example"}),
        entry="check",
        steps=[
            StepSpec(
                id="check",
                block="core.payload_match",
                config={"path": "trigger.payload.type", "equals": "hot"},
                on_success="log",
                on_failure="queue",
            ),
            StepSpec(id="log", block="vault.append_log", config={"summary": "hot event"}),
            StepSpec(
                id="queue",
                block="vault.create_task",
                config={"action": "review cold event {{trigger.payload.id}}"},
            ),
        ],
        created=utcnow(),
        updated=utcnow(),
    )
    base.update(overrides)
    return WorkflowSpec(**base)


# --- schema + graph validation --------------------------------------------------------


def test_graph_validation_catches_bad_refs():
    from nexus.workflows.schema import validate_graph

    spec = _spec(entry="nope")
    assert any("entry" in p for p in validate_graph(spec))

    spec = _spec()
    spec.steps[0].on_success = "ghost"
    assert any("ghost" in p for p in validate_graph(spec))
    assert validate_graph(_spec()) == []


def test_validate_emission_unwraps_wrapper_key():
    """The compile model sometimes nests the draft under one key ({"workflow": {...}});
    _validate_emission peels it, and a genuine schema failure becomes a retryable problem
    instead of an uncaught ValidationError (regression: create_workflow crash)."""
    from nexus.workflows import builder

    flat = {k: getattr(_spec(), k) for k in ("slug", "name", "description", "entry")}
    flat["trigger"] = _spec().trigger.model_dump()
    flat["steps"] = [s.model_dump() for s in _spec().steps]

    draft, problems = builder._validate_emission({"workflow": flat})
    assert draft is not None and problems == []
    assert draft.slug == "test-flow"

    # not a recognized wrapper -> surfaces as problems, never raises
    bad_draft, bad_problems = builder._validate_emission({"nope": 1, "steps": []})
    assert bad_draft is None and bad_problems


def test_registry_refuses_external_send_blocks():
    from nexus.workflows.blocks import Block, BlockKind, register

    with pytest.raises(ValueError, match="external_send"):
        register(
            Block(
                name="evil.send_email",
                kind=BlockKind.action,
                connector="test",
                description="send an email",
                fn=lambda c, x: None,
                external_send=True,
            )
        )


def test_core_catalog_has_triggers_actions_and_no_send():
    from nexus.workflows.blocks import catalog

    names = {b["name"] for b in catalog()}
    assert {"trigger.webhook", "trigger.cron", "vault.create_task", "agent.run"} <= names


# --- store round-trip + lifecycle ------------------------------------------------------


def test_store_roundtrip_and_lifecycle(vault):
    from nexus.workflows import store

    store.save_workflow(_spec())
    loaded = store.load_workflow("test-flow")
    assert loaded is not None and loaded.name == "Test Flow" and loaded.status == "draft"
    assert (vault / "system" / "workflows" / "test-flow.json").is_file()

    spec = store.set_workflow_status("test-flow", "active")
    assert spec.status == "active"
    assert [s.slug for s in store.list_workflows(status="active")] == ["test-flow"]


def test_activation_blocks_unknown_block_refs(vault):
    from nexus.workflows import store
    from nexus.workflows.schema import StepSpec

    spec = _spec()
    spec.steps.append(StepSpec(id="mystery", block="nope.nothing"))
    # save bypassing block validation (graph is still valid) — activation must catch it
    store.save_workflow(spec)
    with pytest.raises(ValueError, match="unknown blocks"):
        store.set_workflow_status("test-flow", "active")


# --- engine: branches, templating, per-step persistence, multi-instance ---------------


def test_engine_runs_true_branch_and_persists(vault):
    from nexus.workflows import engine, store

    run = asyncio.run(
        engine.start_run(_spec(), trigger_payload={"type": "hot", "id": "e1"})
    )
    assert run.status == "succeeded"
    assert [r.step_id for r in run.results] == ["check", "log"]
    assert store.load_run(run.run_id).status == "succeeded"
    assert list((vault / "events").glob("*.md"))  # append_log actually wrote


def test_engine_false_branch_resolves_template_refs(vault):
    from nexus.vault.queries import list_open_tasks
    from nexus.workflows import engine

    run = asyncio.run(
        engine.start_run(_spec(), trigger_payload={"type": "cold", "id": "e42"})
    )
    assert run.status == "succeeded"
    assert [r.step_id for r in run.results] == ["check", "queue"]
    tasks = list_open_tasks()
    assert len(tasks) == 1 and "e42" in tasks[0]["action"]  # {{trigger.payload.id}} resolved


def test_one_workflow_many_concurrent_instances(vault):
    from nexus.workflows import engine, store

    spec = _spec()
    r1 = asyncio.run(engine.start_run(spec, trigger_payload={"type": "hot"}))
    r2 = asyncio.run(engine.start_run(spec, trigger_payload={"type": "cold"}))
    assert r1.run_id != r2.run_id
    assert len(store.list_runs(workflow="test-flow")) == 2


def test_failed_step_without_handler_fails_run(vault):
    from nexus.workflows import engine
    from nexus.workflows.schema import StepSpec

    spec = _spec(entry="boom", steps=[
        StepSpec(id="boom", block="vault.update_entity", config={"name": "x", "kind": "no-kind"})
    ])
    run = asyncio.run(engine.start_run(spec))
    assert run.status == "failed" and "boom" in (run.error or "")


# --- triggers: matching + dispatch integration ----------------------------------------


def test_trigger_matching_fires_only_active_and_matching(vault):
    from nexus.connectors.ingress.envelope import Stimulus
    from nexus.workflows import store
    from nexus.workflows.triggers import fire_matching

    store.save_workflow(_spec())  # draft: must NOT fire
    stim = Stimulus(
        source="example", kind="new_record",
        received_at=datetime.now(UTC), payload={"type": "hot"},
    )
    assert asyncio.run(fire_matching(stim, "supervised")) == []

    store.set_workflow_status("test-flow", "active")
    runs = asyncio.run(fire_matching(stim, "supervised"))
    assert len(runs) == 1 and runs[0].status == "succeeded" and runs[0].tier == "supervised"

    other = Stimulus(source="other", kind="x", received_at=datetime.now(UTC))
    assert asyncio.run(fire_matching(other, "supervised")) == []


def test_cancel_clears_running_instance(vault):
    from nexus.workflows import engine, store
    from nexus.workflows.schema import RunState, utcnow

    run = RunState(
        run_id="stuck1", workflow="test-flow", workflow_version=1,
        current_step="check", started_at=utcnow(), updated_at=utcnow(),
    )
    store.save_run(run)
    assert store.list_runs(status="running")[0].run_id == "stuck1"
    assert engine.cancel_run("stuck1").status == "cancelled"
    assert store.list_runs(status="running") == []


# --- render ----------------------------------------------------------------------------


def test_mermaid_preview_renders_nodes_and_branches():
    from nexus.workflows.render import preview, to_mermaid

    chart = to_mermaid(_spec())
    assert chart.startswith("flowchart TD")
    assert 's_check{"' in chart  # condition renders as a diamond
    assert "-->|yes| s_log" in chart and "-->|no| s_queue" in chart
    assert "```mermaid" in preview(_spec())


# --- builder (LLM) — runs only with a key ----------------------------------------------


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_builder_compiles_nl_to_valid_draft(vault):
    from nexus.workflows import builder
    from nexus.workflows.schema import validate_graph

    spec = asyncio.run(
        builder.create_workflow(
            "When an example webhook of kind new_record arrives, log the event, then "
            "queue a task for me to review it."
        )
    )
    assert spec.status == "draft" and validate_graph(spec) == []
