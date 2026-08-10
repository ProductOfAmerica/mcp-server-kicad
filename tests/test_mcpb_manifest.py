"""Tests for the MCPB bundle manifest.

Both failure modes here are silent: a typo in a ``${user_config.X}`` reference
or in an env var name leaves the setting simply unset, with no error anywhere,
so the tools quietly behave as if the user never filled the field in.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MCPB_DIR = REPO_ROOT / "mcpb"
MANIFEST = json.loads((MCPB_DIR / "manifest.json").read_text(encoding="utf-8"))
ENV = MANIFEST["server"]["mcp_config"]["env"]

# Placeholders; the release workflow stamps both from the version being
# released, so what is committed here is never the shipped value.
PLACEHOLDER = "0.0.0"


def test_user_config_references_resolve():
    """Every ${user_config.X} in env has a matching user_config entry."""
    declared = set(MANIFEST["user_config"])
    referenced = {
        m.group(1) for v in ENV.values() if (m := re.fullmatch(r"\$\{user_config\.(\w+)\}", v))
    }
    assert referenced, "no user_config references found; did env stop using them?"
    assert referenced <= declared, f"undeclared: {sorted(referenced - declared)}"
    assert declared <= referenced, f"declared but never passed: {sorted(declared - referenced)}"


def test_env_var_names_are_read_by_the_server():
    """Every env var the manifest sets is one the package actually reads."""
    sources = "\n".join(
        p.read_text(encoding="utf-8") for p in (REPO_ROOT / "mcp_server_kicad").glob("*.py")
    )
    for name in ENV:
        assert f'"{name}"' in sources, f"{name} is set by the manifest but read nowhere"


def test_optional_config_has_an_empty_default():
    """Without a default, MCPB passes the placeholder through verbatim.

    Measured 2026-08-10 against getMcpConfigForManifest from @anthropic-ai/mcpb
    2.1.2: an unset field with no default yields the literal string
    ``${user_config.kicad_cli_path}`` as the env value, not an empty string.
    That is truthy, so _resolve_config would take it as a real path and
    _find_kicad_cli would return it instead of falling through to PATH and the
    macOS bundle, breaking every CLI-backed tool for the macOS and Linux users
    the field's own description tells to leave it blank. Adding ``default: ""``
    makes the same call yield "", which is falsy and resolves correctly.
    """
    for key, spec in MANIFEST["user_config"].items():
        assert not spec.get("required"), f"{key} is required; this test assumes optional"
        assert spec.get("default") == "", f"{key} needs an empty default"


def test_entry_point_exists():
    assert (MCPB_DIR / MANIFEST["server"]["entry_point"]).is_file()


def test_versions_are_placeholders():
    """Guards the stamped-not-bumped contract; a real version here would rot."""
    assert MANIFEST["version"] == PLACEHOLDER
    pin = f'"mcp-server-kicad=={PLACEHOLDER}"'
    assert pin in (MCPB_DIR / "pyproject.toml").read_text(encoding="utf-8")
