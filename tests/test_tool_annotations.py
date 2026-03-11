"""Verify every MCP tool has correct ToolAnnotations."""

import pytest

from mcp_server_kicad import footprint, pcb, project, schematic, symbol
from mcp_server_kicad._shared import _ADDITIVE, _DESTRUCTIVE, _EXPORT, _READ_ONLY


def _get_annotations(module, tool_name):
    """Get ToolAnnotations for a tool from a server module's mcp instance."""
    tool = module.mcp._tool_manager._tools[tool_name]
    return tool.annotations


# -- symbol.py --

@pytest.mark.parametrize("tool_name", ["list_lib_symbols", "get_symbol_info"])
def test_symbol_read_only(tool_name):
    assert _get_annotations(symbol, tool_name) == _READ_ONLY


@pytest.mark.parametrize("tool_name", ["add_symbol"])
def test_symbol_additive(tool_name):
    assert _get_annotations(symbol, tool_name) == _ADDITIVE


@pytest.mark.parametrize("tool_name", ["export_symbol_svg"])
def test_symbol_export(tool_name):
    assert _get_annotations(symbol, tool_name) == _EXPORT


@pytest.mark.parametrize("tool_name", ["upgrade_symbol_lib"])
def test_symbol_destructive(tool_name):
    assert _get_annotations(symbol, tool_name) == _DESTRUCTIVE


# -- schematic.py --

@pytest.mark.parametrize("tool_name", [
    "get_schematic_info", "list_schematic_items", "get_symbol_pins",
    "get_pin_positions", "get_net_connections", "list_unconnected_pins",
])
def test_schematic_read_only(tool_name):
    assert _get_annotations(schematic, tool_name) == _READ_ONLY


@pytest.mark.parametrize("tool_name", [
    "place_component", "add_wires", "add_label", "add_junctions",
    "add_lib_symbol", "move_component", "set_component_property",
    "add_global_label", "add_no_connect", "add_power_symbol",
    "add_power_rail", "auto_place_decoupling_cap", "add_text",
    "wire_pins_to_net", "connect_pins", "no_connect_pin",
])
def test_schematic_additive(tool_name):
    assert _get_annotations(schematic, tool_name) == _ADDITIVE


@pytest.mark.parametrize("tool_name", [
    "remove_component", "remove_label", "remove_wire", "remove_junction",
])
def test_schematic_destructive(tool_name):
    assert _get_annotations(schematic, tool_name) == _DESTRUCTIVE


@pytest.mark.parametrize("tool_name", [
    "run_erc", "export_schematic", "export_netlist", "export_bom",
])
def test_schematic_export(tool_name):
    assert _get_annotations(schematic, tool_name) == _EXPORT


# -- pcb.py --

@pytest.mark.parametrize("tool_name", [
    "list_pcb_items", "get_board_info", "get_footprint_pads",
])
def test_pcb_read_only(tool_name):
    assert _get_annotations(pcb, tool_name) == _READ_ONLY


@pytest.mark.parametrize("tool_name", [
    "place_footprint", "move_footprint", "add_trace", "add_via",
    "add_pcb_text", "add_pcb_line",
])
def test_pcb_additive(tool_name):
    assert _get_annotations(pcb, tool_name) == _ADDITIVE


@pytest.mark.parametrize("tool_name", ["remove_footprint"])
def test_pcb_destructive(tool_name):
    assert _get_annotations(pcb, tool_name) == _DESTRUCTIVE


@pytest.mark.parametrize("tool_name", [
    "run_drc", "export_pcb", "export_gerbers", "export_gerber",
    "export_3d", "export_positions", "render_3d", "export_pcb_dxf",
    "export_ipc2581",
])
def test_pcb_export(tool_name):
    assert _get_annotations(pcb, tool_name) == _EXPORT


# -- footprint.py --

@pytest.mark.parametrize("tool_name", ["list_lib_footprints", "get_footprint_info"])
def test_footprint_read_only(tool_name):
    assert _get_annotations(footprint, tool_name) == _READ_ONLY


@pytest.mark.parametrize("tool_name", ["export_footprint_svg"])
def test_footprint_export(tool_name):
    assert _get_annotations(footprint, tool_name) == _EXPORT


@pytest.mark.parametrize("tool_name", ["upgrade_footprint_lib"])
def test_footprint_destructive(tool_name):
    assert _get_annotations(footprint, tool_name) == _DESTRUCTIVE


# -- project.py --

@pytest.mark.parametrize("tool_name", ["get_version"])
def test_project_read_only(tool_name):
    assert _get_annotations(project, tool_name) == _READ_ONLY


@pytest.mark.parametrize("tool_name", [
    "create_project", "create_schematic", "create_symbol_library",
    "create_sym_lib_table", "add_hierarchical_sheet",
])
def test_project_additive(tool_name):
    assert _get_annotations(project, tool_name) == _ADDITIVE


@pytest.mark.parametrize("tool_name", ["run_jobset"])
def test_project_export(tool_name):
    assert _get_annotations(project, tool_name) == _EXPORT


# -- Completeness check --

def test_all_tools_have_annotations():
    """Every registered tool must have annotations set (not None)."""
    modules = [symbol, schematic, pcb, footprint, project]
    missing = []
    for mod in modules:
        for name, tool in mod.mcp._tool_manager._tools.items():
            if tool.annotations is None:
                missing.append(f"{mod.__name__}.{name}")
    assert missing == [], f"Tools missing annotations: {missing}"
