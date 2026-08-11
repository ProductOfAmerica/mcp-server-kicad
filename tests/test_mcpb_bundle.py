"""Drive the MCPB bundle the way a desktop host does: as a subprocess over stdio.

Everything else in this suite imports the tools and calls them. That never
exercises the layer the bundle actually is, and every MCPB defect so far has
lived there rather than in tool logic: an unset config field arriving as the
literal string ``${user_config.kicad_cli_path}``, a temp directory getting
packed into the archive, a manifest whose version was stamped wrong.

Deliberately not a mock. The point is a real ``uv run`` against the real
manifest, so what runs here is what a host runs.

Skipped unless uv is installed, and slow: uv builds a fresh environment.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from conftest import EXPECTED_TOOL_COUNT, StdioClient

REPO_ROOT = Path(__file__).parent.parent
MCPB_DIR = REPO_ROOT / "mcpb"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build the bundle env"),
]


def _prepare_bundle(root: Path) -> list[str]:
    """Copy the bundle to ``root``, point it at this working tree, return its argv."""
    shutil.copytree(MCPB_DIR, root, dirs_exist_ok=True)
    # The shipped pyproject pins a PyPI release that does not contain the code
    # under test. Point it at the checkout so this tests HEAD.
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "mcp-server-kicad==" in text, "bundle pyproject stopped pinning the package"
    text = re.sub(
        r"^dependencies = .*$",
        f'dependencies = ["mcp-server-kicad @ file:///{REPO_ROOT.as_posix()}"]',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    pyproject.write_text(text, encoding="utf-8")

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return ["uv", "run", "--directory", str(root), manifest["server"]["entry_point"]]


@pytest.fixture(scope="module")
def bundle():
    # ignore_cleanup_errors because the bundle's .venv holds native extensions.
    # Windows refuses to delete a DLL that is still mapped, and a grandchild
    # process can outlive the uv parent by a moment. The directory is under the
    # OS temp root, so leaving it is a non-event; failing the run over it is not.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        argv = _prepare_bundle(Path(tmp) / "bundle")
        env = {k: v for k, v in os.environ.items() if not k.startswith("KICAD_")}
        with StdioClient(argv, env) as client:
            yield client


def test_bundle_serves_the_whole_tool_surface(bundle):
    """A host talking to the packed manifest sees every tool, not a subset.

    Issue #2 shipped 89 tools instead of the full count, and only a real
    client would have noticed.
    """
    assert bundle.server_info["name"] == "kicad"
    assert bundle.server_info.get("version"), "server reported no version"
    assert len(bundle.tools()) == EXPECTED_TOOL_COUNT


def test_bundle_round_trips_a_real_edit(bundle):
    """Create, write, and read back through the protocol.

    The write goes through _atomic_write in the packed environment, so this is
    also the only place that path is exercised as a subprocess rather than an
    import.
    """
    with tempfile.TemporaryDirectory() as tmp:
        project_dir = Path(tmp) / "bt"
        bundle.call("create_project", {"directory": str(project_dir), "name": "bt"})
        sch = project_dir / "bt.kicad_sch"
        assert sch.is_file()
        before = sch.read_bytes()

        bundle.call(
            "place_component",
            {
                "lib_id": "Device:R",
                "reference": "R1",
                "value": "10k",
                "x": 100,
                "y": 100,
                "schematic_path": str(sch),
                "project_path": str(project_dir / "bt.kicad_pro"),
            },
        )

        after = sch.read_bytes()
        assert after != before
        assert after.startswith(b"(kicad_sch")
        assert after.rstrip().endswith(b")")
        assert b"R1" in after
        assert list(project_dir.glob("*.tmp")) == [], "atomic write left a temp file behind"
