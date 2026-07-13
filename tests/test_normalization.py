"""Reference-note title/tag normalization tests.

The gate must guarantee ONE canonical form regardless of writer (ingest LLM, MCP tool,
script): tags lowercase kebab-case, titles free of trademark glyphs and curly
punctuation, subtitle colons rendered as " - ".
"""

from __future__ import annotations

from datetime import date

from nexus.vault.schema import ReferenceNote, Status


def _note(**kwargs) -> ReferenceNote:
    defaults = dict(
        title="A Note",
        status=Status.draft,
        created=date(2026, 7, 13),
        updated=date(2026, 7, 13),
    )
    return ReferenceNote(**{**defaults, **kwargs})


def test_tags_normalize_to_kebab_case():
    note = _note(tags=["senior care", "Senior-Care", "in-home care", "In_Home  Care", "FAQ®"])
    assert note.tags == ["senior-care", "in-home-care", "faq"]


def test_empty_and_symbol_only_tags_are_dropped():
    assert _note(tags=["", "®", "  ", "valid"]).tags == ["valid"]


def test_title_strips_trademark_glyphs():
    note = _note(title="Seniors Helping Seniors® In-Home Care Services FAQ")
    assert note.title == "Seniors Helping Seniors In-Home Care Services FAQ"


def test_title_subtitle_colon_becomes_dash():
    note = _note(title="Seniors Helping Seniors: In-Home Care for Dementia")
    assert note.title == "Seniors Helping Seniors - In-Home Care for Dementia"


def test_title_curly_punctuation_and_whitespace_normalize():
    note = _note(title="  Alzheimer’s   “Guide” — Overview  ")
    assert note.title == "Alzheimer's \"Guide\" - Overview"


def test_same_brand_three_spellings_converge():
    variants = [
        "Seniors Helping Seniors® Overview",
        "Seniors  Helping Seniors Overview",
        "Seniors Helping Seniors Overview ",
    ]
    assert len({_note(title=t).title for t in variants}) == 1
