"""Every MCP tool's ToolAnnotations, pinned exhaustively.

One table, every registered tool in it exactly once. The completeness test is
the point: the previous version of this file asserted on 92 of 109 tools, and
the 17 with no expectation were where wrong annotations sat unnoticed.

Annotations drive the client's approval gate, so a wrong one is not cosmetic:
readOnlyHint says the tool does not modify its environment, destructiveHint
false says it performs only additive updates, and a client is entitled to skip
confirmation on that basis.
"""

import inspect

import pytest

from mcp_server_kicad import footprint, pcb, project, schematic, symbol
from mcp_server_kicad._shared import _ADDITIVE, _DESTRUCTIVE, _EXPORT, _READ_ONLY

_MODULES = {
    "symbol": symbol,
    "schematic": schematic,
    "pcb": pcb,
    "footprint": footprint,
    "project": project,
}

_PRESETS = {
    "read_only": _READ_ONLY,
    "additive": _ADDITIVE,
    "destructive": _DESTRUCTIVE,
    "export": _EXPORT,
}

# module -> preset -> tool names. Exhaustive by construction: see
# test_every_tool_is_classified_exactly_once.
_EXPECTED: dict[str, dict[str, list[str]]] = {
    "symbol": {
        "read_only": ["list_lib_symbols", "get_symbol_info"],
        "additive": ["add_symbol"],
        "destructive": ["upgrade_symbol_lib"],
        "export": ["export_symbol_svg"],
    },
    "footprint": {
        "read_only": ["list_lib_footprints", "get_footprint_info"],
        "destructive": ["upgrade_footprint_lib"],
        "export": ["export_footprint_svg"],
    },
    "schematic": {
        "read_only": [
            "get_schematic_summary",
            "list_schematic_components",
            "list_schematic_labels",
            "list_schematic_wires",
            "list_schematic_global_labels",
            "list_schematic_hierarchical_labels",
            "list_schematic_sheets",
            "list_schematic_junctions",
            "list_schematic_no_connects",
            "list_schematic_bus_entries",
            "get_symbol_pins",
            "get_pin_positions",
            "get_net_connections",
        ],
        "additive": [
            "place_component",
            "add_wires",
            "add_label",
            "add_junctions",
            "add_lib_symbol",
            "move_component",
            "set_component_property",
            "set_page_size",
            "add_global_label",
            "add_hierarchical_label",
            "add_power_symbol",
            "auto_place_decoupling_cap",
            "add_text",
            "wire_pins_to_net",
            "connect_pins",
            "no_connect_pin",
        ],
        "destructive": [
            "remove_component",
            "remove_label",
            "remove_wire",
            "remove_junction",
            "remove_hierarchical_label",
            "modify_hierarchical_label",
            "remove_text",
            "remove_no_connect",
        ],
        "export": [
            "run_erc",
            "list_unconnected_pins",
            "export_schematic",
            "export_netlist",
            "export_bom",
        ],
    },
    "pcb": {
        "read_only": [
            "list_pcb_footprints",
            "list_pcb_traces",
            "list_pcb_nets",
            "list_pcb_zones",
            "list_pcb_layers",
            "list_pcb_graphic_items",
            "get_board_info",
            "get_footprint_pads",
            "check_placement",
            "get_footprint_bounds",
            "validate_board",
        ],
        "additive": [
            "place_footprint",
            "move_footprint",
            "add_trace",
            "add_via",
            "add_pcb_text",
            "add_pcb_line",
            "add_copper_zone",
            "add_keepout_zone",
            "fill_zones",
            "update_pcb_from_schematic",
            "set_trace_width",
            "add_thermal_vias",
            "set_net_class",
        ],
        "destructive": [
            "remove_footprint",
            "remove_traces",
            "remove_dangling_tracks",
            # `output` is a full file path the caller supplies, unguarded.
            "export_ipc2581",
        ],
        "export": [
            "run_drc",
            "export_pcb",
            "export_gerbers",
            "export_3d",
            "export_positions",
        ],
    },
    "project": {
        "read_only": [
            "validate_hierarchy",
            "is_root_schematic",
            "list_hierarchy",
            "get_sheet_info",
            "trace_hierarchical_net",
            "list_cross_sheet_nets",
            "get_symbol_instances",
            "get_version",
        ],
        "additive": [
            "create_project",
            "create_schematic",
            "create_symbol_library",
            "add_hierarchical_sheet",
            "add_sheet_pin",
            "annotate_schematic",
        ],
        "destructive": [
            "remove_hierarchical_sheet",
            "modify_hierarchical_sheet",
            "remove_sheet_pin",
            "move_hierarchical_sheet",
            "reorder_sheet_pages",
            # Write to a destination the caller names, with no guard on it.
            "create_sym_lib_table",
            "duplicate_sheet",
            "flatten_hierarchy",
        ],
        "export": ["export_hierarchical_netlist", "run_jobset"],
    },
}


# Tools whose behavior fits no preset, so they carry their own ToolAnnotations.
# They are still classified: completeness counts them, and each has a test
# asserting its fields one by one.
_CUSTOM: dict[str, list[str]] = {
    "pcb": ["autoroute_pcb"],
}


def _expected_pairs() -> list[tuple[str, str, str]]:
    """(module, preset, tool) for every entry in the table."""
    return [
        (mod, preset, name)
        for mod, groups in _EXPECTED.items()
        for preset, names in groups.items()
        for name in names
    ]


def _registered(mod: str) -> set[str]:
    return set(_MODULES[mod].mcp._tool_manager._tools)


@pytest.mark.parametrize(
    "mod,preset,name", _expected_pairs(), ids=lambda v: v if isinstance(v, str) else str(v)
)
def test_tool_carries_expected_annotations(mod, preset, name):
    tool = _MODULES[mod].mcp._tool_manager._tools[name]
    assert tool.annotations == _PRESETS[preset]


def test_every_tool_is_classified_exactly_once():
    """No tool may be missing from the table, and none may appear twice."""
    for mod in _MODULES:
        listed = [n for names in _EXPECTED[mod].values() for n in names]
        listed += _CUSTOM.get(mod, [])
        dupes = sorted({n for n in listed if listed.count(n) > 1})
        assert dupes == [], f"{mod}: listed under more than one preset: {dupes}"

        missing = sorted(_registered(mod) - set(listed))
        assert missing == [], f"{mod}: registered but not classified here: {missing}"

        stale = sorted(set(listed) - _registered(mod))
        assert stale == [], f"{mod}: classified here but not registered: {stale}"


def test_all_tools_have_annotations():
    """Every registered tool must have annotations set (not None)."""
    missing = [
        f"{mod.__name__}.{name}"
        for mod in _MODULES.values()
        for name, tool in mod.mcp._tool_manager._tools.items()
        if tool.annotations is None
    ]
    assert missing == [], f"Tools missing annotations: {missing}"


def test_table_covers_the_whole_surface():
    """Guards the guard: a shrinking table must fail, not quietly assert less."""
    listed = sum(len(names) for groups in _EXPECTED.values() for names in groups.values())
    listed += sum(len(names) for names in _CUSTOM.values())
    registered = sum(len(_registered(mod)) for mod in _MODULES)
    assert listed == registered


def test_autoroute_pcb_carries_its_own_hints():
    """openWorldHint true, and the two it cannot honestly claim left unset.

    Asserting `is None` rather than `is False` is the point: an unset hint
    falls back to the spec default, and for a tool that rewrites its output
    file and runs a heuristic router those defaults (destructive true,
    idempotent false) are the accurate readings. Writing False here would be a
    claim the tool cannot support, which is what carrying _EXPORT did.
    """
    annotations = pcb.mcp._tool_manager._tools["autoroute_pcb"].annotations
    assert annotations is not None
    assert annotations.openWorldHint is True
    assert annotations.readOnlyHint is False
    assert annotations.destructiveHint is None
    assert annotations.idempotentHint is None
    assert annotations != _EXPORT


def test_read_only_tools_do_not_write_output_files():
    """readOnlyHint says the tool does not modify its environment.

    A tool that hands kicad-cli --output creates and clobbers a file, so it is
    not read-only however much its name reads like a query. This is the check
    that caught list_unconnected_pins, which ran the same kicad-cli invocation
    as run_erc while claiming to be read-only.
    """
    writers = [
        name
        for mod in _MODULES.values()
        for name, tool in mod.mcp._tool_manager._tools.items()
        if tool.annotations.readOnlyHint and "--output" in inspect.getsource(tool.fn)
    ]
    assert writers == [], f"readOnlyHint tools that write an output file: {writers}"
