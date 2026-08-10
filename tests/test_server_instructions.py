"""Every tool name a server's ``instructions`` mentions must actually exist.

``instructions`` reaches the model as context, so a name that does not resolve
sends it into ``tools/call`` for an unknown tool. The spec classes that as a
protocol error, the category models are least able to recover from, unlike a
tool execution error which carries actionable feedback.
"""

import re

from mcp_server_kicad import footprint, pcb, project, schematic, server, symbol

# Servers whose instructions are checked. The unified server names no tools of
# its own today; it is included so that stays true.
_SERVERS = {
    "footprint": footprint.mcp,
    "pcb": pcb.mcp,
    "project": project.mcp,
    "schematic": schematic.mcp,
    "symbol": symbol.mcp,
    "unified": server.mcp,
}

_TOOL_MODULES = [footprint, pcb, project, schematic, symbol]

# snake_case tokens that are deliberately not tool names: file extensions the
# rules tell the model never to touch, and tool parameters named in prose.
# Explicit over clever — an unrecognised token should fail and force a decision,
# which is how the five phantom names were found.
_NON_TOOL_TOKENS = {
    "kicad_mod",
    "kicad_pcb",
    "kicad_prl",
    "kicad_pro",
    "kicad_sch",
    "kicad_sym",
    "lib_id",
    "pcb_path",
    "project_path",
}

_SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _registered_tools() -> set[str]:
    """Live tool names across every server module."""
    return {name for mod in _TOOL_MODULES for name in mod.mcp._tool_manager._tools}


def _unresolved(instructions: str, tools: set[str]) -> list[str]:
    """Tool-shaped tokens in *instructions* that are neither a tool nor allowlisted."""
    found = set(_SNAKE_CASE.findall(instructions or ""))
    return sorted(found - tools - _NON_TOOL_TOKENS)


def test_instructions_name_only_real_tools():
    tools = _registered_tools()
    bad = {name: _unresolved(mcp.instructions or "", tools) for name, mcp in _SERVERS.items()}
    bad = {name: tokens for name, tokens in bad.items() if tokens}
    assert bad == {}, (
        "instructions mention names that are not registered tools "
        f"(add real ones to the tool surface, prose ones to _NON_TOOL_TOKENS): {bad}"
    )


def test_scanner_would_catch_a_phantom_name():
    """The scanner fails on a bad name, so a green suite means something."""
    tools = _registered_tools()
    assert _unresolved("Use upgrade_fp_lib instead.", tools) == ["upgrade_fp_lib"]
    assert _unresolved("Use upgrade_footprint_lib instead.", tools) == []
