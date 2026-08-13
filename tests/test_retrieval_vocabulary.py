"""The words a user types reach some tool's searchable text.

Under a host that defers tool schemas, the model is given tool *names* first and
retrieves over name plus description. That text stops being documentation and
becomes the retrieval key, so a word no tool contains is a tool the user cannot
reach by asking for it in their own words.

Measured in Claude Desktop on 2026-08-13, asked to "place a 10k resistor":

    tool_search("KiCad place resistor schematic")
      -> no_connect_pin, list_schematic_components, add_power_symbol,
         auto_place_decoupling_cap, get_pin_positions

place_component was not in the result. Three searches were needed to find it,
because no tool description contained the word "resistor". Ten of the fourteen
absent words were part names.

This cuts against the earlier finding recorded for this repo, that descriptions
do not drive tool selection: that was measured with all 109 tools in context at
once, where the name carries the choice. Deferred loading is a different regime,
and in it the description selects.

Deliberately a vocabulary check, not a ranking check. Ranking belongs to the
host's retriever and is not ours to assert; presence is ours, and presence is
what was actually missing.
"""

from __future__ import annotations

import pytest

from mcp_server_kicad import footprint, pcb, project, schematic, symbol

_MODULES = (schematic, pcb, project, symbol, footprint)

#: What a user says, and the tool that had better be reachable by saying it.
#: Each entry is (word, tool that should serve it). The word is matched against
#: every tool's searchable text, and the named tool must be among the matches,
#: so a word cannot be satisfied by an unrelated tool happening to contain it.
_VOCABULARY = [
    ("resistor", "place_component"),
    ("capacitor", "place_component"),
    ("inductor", "place_component"),
    ("diode", "place_component"),
    ("transistor", "place_component"),
    ("crystal", "place_component"),
    ("connector", "place_component"),
    ("microcontroller", "place_component"),
    ("ground plane", "add_copper_zone"),
    ("soldermask", "list_pcb_layers"),
    ("silkscreen", "list_pcb_layers"),
    ("gerber", "export_gerbers"),
    ("netlist", "export_netlist"),
    ("footprint", "place_footprint"),
    ("trace", "add_trace"),
    ("via", "add_via"),
]


def _searchable() -> dict[str, str]:
    """Name plus description, which is all a retriever sees before a fetch."""
    out: dict[str, str] = {}
    for mod in _MODULES:
        for name, tool in mod.mcp._tool_manager._tools.items():
            out[name] = (name + " " + (tool.fn.__doc__ or "")).lower()
    return out


def test_the_corpus_is_populated():
    """Guards the guard: an empty corpus would pass nothing and fail nothing."""
    corpus = _searchable()
    assert len(corpus) >= 100, f"only {len(corpus)} tools"
    assert all(corpus.values()), "some tool has no searchable text at all"


@pytest.mark.parametrize(("word", "tool"), _VOCABULARY, ids=[w for w, _ in _VOCABULARY])
def test_the_word_reaches_its_tool(word: str, tool: str):
    corpus = _searchable()
    assert tool in corpus, f"{tool} is not a tool any more; fix this table"
    hits = [name for name, text in corpus.items() if word in text]
    assert hits, (
        f'no tool mentions "{word}", so a user asking for it in those words'
        f" reaches nothing. It belongs in {tool}'s description."
    )
    assert tool in hits, (
        f'"{word}" appears only in {hits}, not in {tool}, which is the tool that actually does it.'
    )
