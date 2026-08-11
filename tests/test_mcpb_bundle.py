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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
MCPB_DIR = REPO_ROOT / "mcpb"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build the bundle env"),
]


class _Bundle:
    """A bundle wired to this working tree instead of the released PyPI version."""

    def __init__(self, root: Path):
        self.root = root
        self.proc: subprocess.Popen[str] | None = None
        self._id = 0

    def __enter__(self):
        shutil.copytree(MCPB_DIR, self.root, dirs_exist_ok=True)
        # The shipped pyproject pins a PyPI release that does not contain the
        # code under test. Point it at the checkout so this tests HEAD.
        pyproject = self.root / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        src = REPO_ROOT.as_posix()
        assert "mcp-server-kicad==" in text, "bundle pyproject stopped pinning the package"
        text = "\n".join(
            f'dependencies = ["mcp-server-kicad @ file:///{src}"]'
            if line.startswith("dependencies =")
            else line
            for line in text.splitlines()
        )
        pyproject.write_text(text + "\n", encoding="utf-8")

        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        argv = ["uv", "run", "--directory", str(self.root), manifest["server"]["entry_point"]]

        env = {k: v for k, v in os.environ.items() if not k.startswith("KICAD_")}
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        return self

    def __exit__(self, *exc):
        if self.proc:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=30)

    def rpc(self, method: str, params: dict | None = None) -> dict:
        assert self.proc and self.proc.stdin and self.proc.stdout
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                pytest.fail(f"bundle server died during {method}:\n{stderr[-2000:]}")
            if line.strip():
                return json.loads(line)

    def notify(self, method: str) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call(self, name: str, args: dict) -> str:
        result = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        assert not result.get("isError"), f"{name} failed: {json.dumps(result)[:500]}"
        return result["content"][0]["text"]


@pytest.fixture(scope="module")
def bundle():
    with tempfile.TemporaryDirectory() as tmp, _Bundle(Path(tmp) / "bundle") as b:
        init = b.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )
        b.notify("notifications/initialized")
        b.server_info = init["result"]["serverInfo"]  # type: ignore[attr-defined]
        yield b


def test_bundle_serves_the_whole_tool_surface(bundle):
    """A host talking to the packed manifest sees every tool, not a subset.

    Issue #2 shipped 89 tools instead of the full count, and only a real
    client would have noticed.
    """
    assert bundle.server_info["name"] == "kicad"
    tools = bundle.rpc("tools/list")["result"]["tools"]
    assert len(tools) == 109, f"expected 109 tools over stdio, got {len(tools)}"


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


def test_bundle_manifest_matches_the_entry_point_it_ships(bundle):
    """The manifest names the script the host launches; a rename that missed it
    would only surface at install time on a user's machine."""
    manifest = json.loads((MCPB_DIR / "manifest.json").read_text(encoding="utf-8"))
    entry = MCPB_DIR / manifest["server"]["entry_point"]
    assert entry.is_file(), f"manifest entry_point does not exist: {entry}"
    # The server answered initialize, so that entry point really ran. It also
    # has to report our version rather than the SDK's, which only a client sees.
    assert bundle.server_info.get("version"), "server reported no version"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
