"""Tests for the MCPB bundle manifest.

Both failure modes here are silent: a typo in a ``${user_config.X}`` reference
or in an env var name leaves the setting simply unset, with no error anywhere,
so the tools quietly behave as if the user never filled the field in.
"""

import json
import re
import struct
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MCPB_DIR = REPO_ROOT / "mcpb"
MANIFEST = json.loads((MCPB_DIR / "manifest.json").read_text(encoding="utf-8"))
ENV = MANIFEST["server"]["mcp_config"]["env"]

# Placeholders; the release workflow stamps both from the version being
# released, so what is committed here is never the shipped value.
PLACEHOLDER = "0.0.0"

#: The eight bytes every PNG starts with.
PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])


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


def test_the_icon_the_manifest_names_is_actually_in_the_bundle_dir():
    """A missing icon is silent: the manifest still validates and the bundle
    still packs, and the host just shows a default tile."""
    icon = MANIFEST.get("icon")
    assert icon, "manifest declares no icon"
    assert (MCPB_DIR / icon).is_file(), f"{icon} is named by the manifest but not in mcpb/"


def test_the_icon_is_a_square_png_with_alpha():
    """Square because the host draws it in a fixed tile, and alpha because the
    host draws that tile in its own theme.

    The source artwork at .github/assets/logo.png is colour type 2: a white
    line-art mark with near-black baked in across 65% of the canvas. Shipped as
    the icon it renders as a black sticker in a light theme while every icon
    beside it adapts. The bundle icon is derived from it, cropped to the glyph
    with luminance turned into alpha.
    """
    raw = (MCPB_DIR / MANIFEST["icon"]).read_bytes()
    assert raw[:8] == PNG_MAGIC, "not a PNG"
    width, height = struct.unpack(">II", raw[16:24])
    colour_type = raw[25]
    assert width == height, f"{width}x{height} is not square"
    assert width >= 128, f"{width}px is small for a tile"
    # 4 is greyscale+alpha, 6 is truecolour+alpha; 0 and 2 carry none.
    assert colour_type in (4, 6), (
        f"colour type {colour_type} carries no alpha, so the icon ships its own"
        " background and cannot sit on the host's tile in both themes"
    )


def test_the_icon_is_not_mostly_margin():
    """The defect this replaced: measured on the source logo, the white glyph
    sat at (50,66)-(461,445) inside 512x512, so roughly 12% of the tile was dead
    black border before the host added its own padding around it.

    Checked by file size rather than by decoding pixels, which keeps this on the
    standard library. A tightly cropped line-art mark with alpha is a few
    kilobytes; the opaque, margin-padded original was 100 kB. The derivation is
    recorded in docs/adr-cst-substrate.md if the logo ever changes.
    """
    size = (MCPB_DIR / MANIFEST["icon"]).stat().st_size
    assert size < 60_000, (
        f"{size} bytes suggests an opaque or uncropped image; the derived icon is a few kB"
    )
