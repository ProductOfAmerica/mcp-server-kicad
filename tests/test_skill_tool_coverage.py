"""Test that every MCP tool is documented in at least one skill file."""

import re
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"
SRC_DIR = Path(__file__).parent.parent / "mcp_server_kicad"

# Tools that are intentionally undocumented (none currently)
EXCLUDED_TOOLS: set[str] = set()


def _get_registered_tools() -> set[str]:
    """Parse all @mcp.tool decorated function names from source.

    Two-pass approach (explicit > clever):
    1. Find lines with @mcp.tool
    2. Find the next 'def' line after each decorator
    """
    tools: set[str] = set()
    for py_file in SRC_DIR.glob("*.py"):
        lines = py_file.read_text().splitlines()
        in_decorator = False
        for line in lines:
            if "@mcp.tool(" in line:
                in_decorator = True
                continue
            if in_decorator and line.strip().startswith("def "):
                match = re.match(r"\s*def\s+(\w+)\s*\(", line)
                if match:
                    tools.add(match.group(1))
                in_decorator = False
    return tools - EXCLUDED_TOOLS


def _get_documented_tools(registered: set[str]) -> set[str]:
    """Check which registered tool names appear in any SKILL.md file.

    Only checks for known tool names to avoid false positives from
    backtick-quoted parameter names, file paths, etc.
    """
    all_skill_text = ""
    for skill_file in SKILLS_DIR.rglob("SKILL.md"):
        all_skill_text += skill_file.read_text()

    return {name for name in registered if f"`{name}`" in all_skill_text}


def test_all_tools_documented():
    """Every @mcp.tool must appear in at least one SKILL.md."""
    registered = _get_registered_tools()
    documented = _get_documented_tools(registered)
    missing = registered - documented
    assert not missing, "Tools registered but not documented in any skill:\n" + "\n".join(
        f"  - {t}" for t in sorted(missing)
    )
