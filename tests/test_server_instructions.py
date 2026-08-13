"""Every tool name a server's ``instructions`` mentions must actually exist.

``instructions`` reaches the model as context, so a name that does not resolve
sends it into ``tools/call`` for an unknown tool. The spec classes that as a
protocol error, the category models are least able to recover from, unlike a
tool execution error which carries actionable feedback.
"""

import re
import tempfile
from pathlib import Path

from mcp_server_kicad import footprint, pcb, project, schematic, server, symbol

_TOOL_MODULES = [footprint, pcb, project, schematic, symbol]

# The unified server registers no tools of its own until main() runs. It names
# a dozen entry points in its instructions, so a host that defers tool schemas
# can fetch one by name instead of searching blind, and this scanner is what
# stops a rename from leaving a phantom among them.
_SERVER_MODULES = [*_TOOL_MODULES, server]

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
    bad = {m.__name__: _unresolved(m.mcp.instructions or "", tools) for m in _SERVER_MODULES}
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


CONFIGURABLE_PATH_PARAMS = (
    "schematic_path",
    "pcb_path",
    "symbol_lib_path",
    "footprint_path",
    "output_dir",
)


def test_configurable_path_params_say_they_are_optional():
    """The Args line has to say it, because nothing else the model reads does.

    Measured in Claude Desktop after the FILE ACCESS rules below landed: the
    model stopped demanding a connected folder and instead asked the user for
    the path, which is the documented fallback for when nothing is configured.
    A path *was* configured, and the schema said so, but the only prose it had
    was a flat "Path to .kicad_sch file", which reads as a requirement.

    Parameter descriptions never reach inputSchema at all; only the tool-level
    description carries the Args block. So this is the one place the model is
    guaranteed to see it, whatever the client does with instructions.
    """
    missing = []
    for mod in _TOOL_MODULES:
        for name, tool in mod.mcp._tool_manager._tools.items():
            props = tool.parameters.get("properties", {})
            for param in CONFIGURABLE_PATH_PARAMS:
                if param not in props:
                    continue
                args_line = [
                    ln
                    for ln in (tool.description or "").splitlines()
                    if ln.strip().startswith(f"{param}:")
                ]
                if not args_line or "Optional;" not in args_line[0] + (tool.description or ""):
                    missing.append(f"{name}.{param}")
    assert missing == [], (
        "these path parameters have a configurable default but their docstring"
        f" never says they can be omitted: {sorted(set(missing))}"
    )


def test_every_server_says_it_needs_no_folder_permission():
    """Measured failure, not a hypothetical.

    In Claude Desktop the model twice refused to edit a schematic, asking the
    user to "Add folder" first, while looking at a place_component schema whose
    schematic_path default already held the configured absolute path. Hosts that
    gate their own file tools behind a permission flow teach that reflex, and
    each server's CRITICAL RULES (never touch these files directly) read as
    confirmation of it. The counter-statement has to reach the model on every
    server, so it is appended in build_server rather than copied five times.
    """
    for mod in _SERVER_MODULES:
        text = mod.mcp.instructions or ""
        assert "FILE ACCESS:" in text, mod.__name__
        assert "do NOT need" in text, mod.__name__
        assert "NEVER ask the user to connect" in text, mod.__name__


class TestConfiguredDefaultsAreNamed:
    """The configured paths appear in the instructions, not only in the schemas.

    Every path default has always been in each tool's inputSchema. That is
    invisible to a host that defers tool schemas and gives the model tool names
    first, which is what Claude Desktop does, so the model decides whether to
    ask before it has fetched a single schema.

    Measured in Desktop on 2026-08-13 with all three paths configured and
    reaching the server: asked to place a resistor and run ERC, the model replied
    "What's the path to your schematic file?" and offered
    "/home/claude/my_project/schematic.kicad_sch" as the example, a Linux path
    invented on a Windows machine. Instructions arrive with initialize, before
    any schema fetch, so they are the one place such a host is sure to read.
    """

    def test_a_configured_path_is_named(self, monkeypatch):
        text = self._instructions(monkeypatch, {"KICAD_SCH_PATH": r"D:\proj\thing.kicad_sch"})
        assert r"D:\proj\thing.kicad_sch" in text
        assert "no path argument at all" in text
        assert "do NOT guess" in text

    def test_every_configured_kind_is_named(self, monkeypatch):
        env = {
            "KICAD_SCH_PATH": r"D:\p\a.kicad_sch",
            "KICAD_PCB_PATH": r"D:\p\a.kicad_pcb",
            "KICAD_SYM_LIB": r"D:\p\a.kicad_sym",
            "KICAD_FP_LIB": r"D:\p\a.pretty",
            "KICAD_OUTPUT_DIR": r"D:\p",
        }
        text = self._instructions(monkeypatch, env)
        for path in env.values():
            assert path in text, f"{path} is configured but never named"

    def test_nothing_configured_says_so_and_says_what_to_do(self, monkeypatch):
        """The empty case must not read as "no defaults section, so who knows"."""
        text = self._instructions(monkeypatch, {})
        assert "Nothing is configured" in text
        assert "create_project" in text

    def test_an_unset_path_is_not_listed(self, monkeypatch):
        """A blank field must not appear as an empty bullet."""
        text = self._instructions(monkeypatch, {"KICAD_SCH_PATH": r"D:\p\a.kicad_sch"})
        section = text.split("CONFIGURED DEFAULTS:")[1]
        assert "- board:" not in section
        assert "- symbol library:" not in section

    @staticmethod
    def _instructions(monkeypatch, env: dict[str, str]) -> str:
        """Rebuild a server under *env*, since the paths resolve at import."""
        import importlib

        import mcp_server_kicad._shared as shared

        for var in (
            "KICAD_SCH_PATH",
            "KICAD_PCB_PATH",
            "KICAD_SYM_LIB",
            "KICAD_FP_LIB",
            "KICAD_OUTPUT_DIR",
        ):
            monkeypatch.delenv(var, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        # A directory with no *.kicad_pro, so auto-detect cannot add paths the
        # test never set and make an assertion pass for the wrong reason.
        monkeypatch.setattr(shared, "_cwd", lambda: Path(tempfile.mkdtemp()))
        reloaded = importlib.reload(shared)
        try:
            return reloaded.build_server("probe", "X").instructions or ""
        finally:
            monkeypatch.undo()
            importlib.reload(shared)


def test_descriptions_do_not_ship_internal_rationale():
    """A tool description is product surface, not a changelog.

    Every description is loaded into the model's context, and under a host that
    defers schemas it is also the retrieval key, so a sentence about when a bug
    was fixed costs the user context and buys them nothing.

    Written after shipping exactly that. v0.17.3 put "measured in Claude Desktop
    2026-08-13: three tool_search calls before this tool surfaced" into
    place_component's description, where a user reading the loaded tool saw it:
    520 chars of summary, 313 of them explaining a decision to the maintainer.
    The reasoning is worth keeping, so it moved to a comment above the tool.

    Deliberately a narrow marker list. A description that legitimately needs a
    date can be made to pass, but it has to be a decision rather than a habit.
    """
    markers = re.compile(
        r"20\d\d-\d\d-\d\d|silently ignored|regression|\bslice \d|ponytail|\btest_\w+",
        re.I,
    )
    offenders = {}
    for mod in _TOOL_MODULES:
        for name, tool in mod.mcp._tool_manager._tools.items():
            summary = (tool.fn.__doc__ or "").split("Args:")[0]
            found = sorted({m.group(0).lower() for m in markers.finditer(summary)})
            if found:
                offenders[name] = found
    assert offenders == {}, (
        "these descriptions carry maintainer rationale into every user's context;"
        " move it to a comment above the tool: " + repr(offenders)
    )
