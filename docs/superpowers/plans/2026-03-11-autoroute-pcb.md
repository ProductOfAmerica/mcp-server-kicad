# Autoroute PCB Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `autoroute_pcb` MCP tool that integrates the Freerouting autorouter for automated PCB trace routing.

**Architecture:** A new `_freerouting.py` helper module handles JAR discovery/download, Java version checking, and Freerouting subprocess invocation. The `autoroute_pcb` tool in `pcb.py` orchestrates the workflow: count existing traces via kiutils, export DSN via pcbnew subprocess, run Freerouting, import SES via pcbnew subprocess, count new traces, optionally run DRC.

**Tech Stack:** Python 3.10+, kiutils (board I/O), pcbnew (DSN/SES conversion via subprocess), Freerouting JAR (autorouting via subprocess), Java 17+ runtime.

**Spec:** `docs/superpowers/specs/2026-03-11-autoroute-pcb-design.md`

---

## Chunk 1: Freerouting Helper Module

### Task 1: Create `_freerouting.py` — Java detection

**Files:**
- Create: `mcp_server_kicad/_freerouting.py`
- Create: `tests/test_freerouting.py`

- [ ] **Step 1: Write failing test for `check_java`**

```python
# tests/test_freerouting.py
"""Tests for Freerouting helper module."""

import subprocess
from unittest.mock import patch

import pytest

from mcp_server_kicad._freerouting import check_java


class TestCheckJava:
    def test_java_found_valid_version(self):
        mock_result = subprocess.CompletedProcess(
            args=["java", "-version"],
            returncode=0,
            stdout="",
            stderr='openjdk version "21.0.1" 2023-10-17',
        )
        with patch("subprocess.run", return_value=mock_result):
            result = check_java()
            assert result is None  # no error

    def test_java_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = check_java()
            assert "Java" in result
            assert "apt install" in result

    def test_java_too_old(self):
        mock_result = subprocess.CompletedProcess(
            args=["java", "-version"],
            returncode=0,
            stdout="",
            stderr='openjdk version "11.0.2" 2019-01-15',
        )
        with patch("subprocess.run", return_value=mock_result):
            result = check_java()
            assert "17" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_freerouting.py::TestCheckJava -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server_kicad._freerouting'`

- [ ] **Step 3: Implement `check_java`**

```python
# mcp_server_kicad/_freerouting.py
"""Freerouting autorouter integration — JAR management, Java checks, subprocess invocation."""

from __future__ import annotations

import re
import subprocess


def check_java() -> str | None:
    """Check that Java 17+ is available. Returns error message or None if OK."""
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return (
            "Java runtime not found. Autorouting requires Java 17+. "
            "Install with: apt install default-jre"
        )

    version_output = result.stderr + result.stdout
    match = re.search(r'"(\d+)[\.\d]*"', version_output)
    if not match:
        return f"Could not parse Java version from: {version_output.strip()}"

    major = int(match.group(1))
    if major < 17:
        return (
            f"Java {major} found but Freerouting requires Java 17+. "
            "Upgrade with: apt install default-jre"
        )
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_freerouting.py::TestCheckJava -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/_freerouting.py tests/test_freerouting.py
git commit -m "feat(autoroute): add Java version detection"
```

---

### Task 2: JAR discovery and download

**Files:**
- Modify: `mcp_server_kicad/_freerouting.py`
- Modify: `tests/test_freerouting.py`

- [ ] **Step 1: Write failing tests for `find_jar` and `ensure_jar`**

Add to `tests/test_freerouting.py`:

```python
import os
from pathlib import Path
from unittest.mock import patch

from mcp_server_kicad._freerouting import ensure_jar, find_jar


class TestFindJar:
    def test_env_var_override(self, tmp_path):
        jar = tmp_path / "custom.jar"
        jar.touch()
        with patch.dict(os.environ, {"FREEROUTING_JAR": str(jar)}):
            assert find_jar() == str(jar)

    def test_env_var_missing_file(self):
        with patch.dict(os.environ, {"FREEROUTING_JAR": "/nonexistent/fr.jar"}):
            assert find_jar() is None

    def test_cached_jar(self, tmp_path):
        cache_dir = tmp_path / ".local" / "share" / "mcp-server-kicad"
        cache_dir.mkdir(parents=True)
        jar = cache_dir / "freerouting.jar"
        jar.touch()
        with patch("mcp_server_kicad._freerouting._cache_dir", return_value=cache_dir):
            assert find_jar() == str(jar)

    def test_no_jar(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with (
            patch.dict(os.environ, {"FREEROUTING_JAR": ""}, clear=False),
            patch("mcp_server_kicad._freerouting._cache_dir", return_value=empty_dir),
        ):
            assert find_jar() is None


class TestEnsureJar:
    def test_already_exists(self, tmp_path):
        jar = tmp_path / "freerouting.jar"
        jar.touch()
        with patch("mcp_server_kicad._freerouting.find_jar", return_value=str(jar)):
            path, err = ensure_jar()
            assert path == str(jar)
            assert err is None

    def test_download_success(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        jar_path = str(cache_dir / "freerouting.jar")

        with (
            patch("mcp_server_kicad._freerouting.find_jar", side_effect=[None, jar_path]),
            patch("mcp_server_kicad._freerouting._download_jar", return_value=jar_path),
        ):
            path, err = ensure_jar()
            assert path == jar_path
            assert err is None

    def test_download_failure(self, tmp_path):
        with (
            patch("mcp_server_kicad._freerouting.find_jar", return_value=None),
            patch(
                "mcp_server_kicad._freerouting._download_jar",
                side_effect=RuntimeError("Network error"),
            ),
        ):
            path, err = ensure_jar()
            assert path is None
            assert "Network error" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_freerouting.py::TestFindJar tests/test_freerouting.py::TestEnsureJar -v`
Expected: FAIL — `ImportError: cannot import name 'ensure_jar' from 'mcp_server_kicad._freerouting'`

- [ ] **Step 3: Implement `find_jar`, `_cache_dir`, `_download_jar`, and `ensure_jar`**

Add to `mcp_server_kicad/_freerouting.py`:

```python
import json
import os
from pathlib import Path
from urllib.request import urlopen, Request

_GITHUB_RELEASES_URL = "https://api.github.com/repos/freerouting/freerouting/releases/latest"


def _cache_dir() -> Path:
    """Return the cache directory for Freerouting JAR."""
    return Path.home() / ".local" / "share" / "mcp-server-kicad"


def find_jar() -> str | None:
    """Find the Freerouting JAR. Returns path or None."""
    # 1. Environment variable
    env_jar = os.environ.get("FREEROUTING_JAR")
    if env_jar and Path(env_jar).is_file():
        return env_jar

    # 2. Cached download
    cached = _cache_dir() / "freerouting.jar"
    if cached.is_file():
        return str(cached)

    return None


def _download_jar() -> str:
    """Download the latest Freerouting JAR from GitHub releases. Returns path."""
    req = Request(_GITHUB_RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=30) as resp:
        release = json.loads(resp.read())

    # Find the executable JAR asset
    jar_asset = None
    for asset in release.get("assets", []):
        name = asset["name"]
        if name.endswith(".jar") and "javadoc" not in name and "sources" not in name:
            jar_asset = asset
            break

    if not jar_asset:
        raise RuntimeError("No JAR asset found in latest Freerouting release")

    download_url = jar_asset["browser_download_url"]
    dest = _cache_dir() / "freerouting.jar"
    dest.parent.mkdir(parents=True, exist_ok=True)

    req = Request(download_url)
    with urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())

    return str(dest)


def ensure_jar() -> tuple[str | None, str | None]:
    """Ensure the Freerouting JAR is available. Downloads if needed.

    Returns (jar_path, error_message). One of them is always None.
    """
    jar = find_jar()
    if jar:
        return jar, None

    try:
        _download_jar()
    except Exception as exc:
        return None, (
            f"Failed to download Freerouting: {exc}. "
            "Download manually from https://github.com/freerouting/freerouting/releases "
            "and set FREEROUTING_JAR environment variable."
        )

    jar = find_jar()
    if jar:
        return jar, None
    return None, "JAR download appeared to succeed but file not found."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_freerouting.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/_freerouting.py tests/test_freerouting.py
git commit -m "feat(autoroute): add Freerouting JAR discovery and download"
```

---

### Task 3: pcbnew subprocess helper

**Files:**
- Modify: `mcp_server_kicad/_freerouting.py`
- Modify: `tests/test_freerouting.py`

- [ ] **Step 1: Write failing tests for `find_pcbnew_python` and `export_dsn` / `import_ses`**

Add to `tests/test_freerouting.py`:

```python
import mcp_server_kicad._freerouting as _fr_module
from mcp_server_kicad._freerouting import export_dsn, find_pcbnew_python, import_ses


class TestFindPcbnewPython:
    @pytest.fixture(autouse=True)
    def _reset_pcbnew_cache(self):
        _fr_module._pcbnew_cache = None
        yield
        _fr_module._pcbnew_cache = None

    def test_direct_import_works(self):
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            python, env = find_pcbnew_python()
            assert python is not None

    def test_no_pcbnew_available(self):
        with patch("subprocess.run", side_effect=Exception("fail")):
            python, env = find_pcbnew_python()
            assert python is None


class TestExportDsn:
    def test_success(self, tmp_path):
        pcb_path = str(tmp_path / "board.kicad_pcb")
        dsn_path = str(tmp_path / "board.dsn")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("mcp_server_kicad._freerouting.find_pcbnew_python", return_value=("python3", None)):
            with patch("subprocess.run", return_value=mock_result):
                err = export_dsn(pcb_path, dsn_path)
                assert err is None

    def test_pcbnew_not_found(self, tmp_path):
        with patch("mcp_server_kicad._freerouting.find_pcbnew_python", return_value=(None, None)):
            err = export_dsn(str(tmp_path / "b.kicad_pcb"), str(tmp_path / "b.dsn"))
            assert "pcbnew" in err.lower() or "KiCad" in err


class TestImportSes:
    def test_success(self, tmp_path):
        pcb_path = str(tmp_path / "board.kicad_pcb")
        ses_path = str(tmp_path / "board.ses")
        out_path = str(tmp_path / "board_routed.kicad_pcb")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("mcp_server_kicad._freerouting.find_pcbnew_python", return_value=("python3", None)):
            with patch("subprocess.run", return_value=mock_result):
                err = import_ses(pcb_path, ses_path, out_path)
                assert err is None

    def test_subprocess_fails(self, tmp_path):
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="pcbnew error")
        with patch("mcp_server_kicad._freerouting.find_pcbnew_python", return_value=("python3", None)):
            with patch("subprocess.run", return_value=mock_result):
                err = import_ses(
                    str(tmp_path / "b.kicad_pcb"),
                    str(tmp_path / "b.ses"),
                    str(tmp_path / "b_routed.kicad_pcb"),
                )
                assert err is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_freerouting.py::TestFindPcbnewPython tests/test_freerouting.py::TestExportDsn tests/test_freerouting.py::TestImportSes -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `find_pcbnew_python`, `export_dsn`, `import_ses`**

Add to `mcp_server_kicad/_freerouting.py`:

```python
_KICAD_PYTHON_PATHS = [
    "/usr/lib/kicad/lib/python3/dist-packages",
    "/usr/lib/python3/dist-packages",
]

_pcbnew_cache: tuple[str | None, dict | None] | None = None


def find_pcbnew_python() -> tuple[str | None, dict | None]:
    """Find a Python interpreter that can import pcbnew.

    Returns (python_path, env_dict) or (None, None).
    Caches result after first successful probe.
    """
    global _pcbnew_cache
    if _pcbnew_cache is not None:
        return _pcbnew_cache

    # Try with KICAD_PYTHON env var first
    kicad_python = os.environ.get("KICAD_PYTHON")
    if kicad_python:
        try:
            result = subprocess.run(
                [kicad_python, "-c", "import pcbnew"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                _pcbnew_cache = (kicad_python, None)
                return _pcbnew_cache
        except Exception:
            pass

    # Try bare python3 first (no PYTHONPATH)
    try:
        result = subprocess.run(
            ["python3", "-c", "import pcbnew"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            _pcbnew_cache = ("python3", None)
            return _pcbnew_cache
    except Exception:
        pass

    # Try with known KiCad PYTHONPATH locations
    for path in _KICAD_PYTHON_PATHS:
        if not Path(path).is_dir():
            continue
        env = {**os.environ, "PYTHONPATH": path}
        try:
            result = subprocess.run(
                ["python3", "-c", "import pcbnew"],
                capture_output=True, text=True, timeout=10, env=env,
            )
            if result.returncode == 0:
                _pcbnew_cache = ("python3", env)
                return _pcbnew_cache
        except Exception:
            pass

    _pcbnew_cache = (None, None)
    return _pcbnew_cache


def export_dsn(pcb_path: str, dsn_path: str) -> str | None:
    """Export a KiCad PCB to Specctra DSN format via pcbnew subprocess.

    Returns error message or None on success.
    """
    python, env = find_pcbnew_python()
    if not python:
        return (
            "KiCad Python bindings (pcbnew) not found. "
            "Ensure KiCad is installed. Set KICAD_PYTHON env var if needed."
        )

    script = (
        "import pcbnew; "
        f"b = pcbnew.LoadBoard({pcb_path!r}); "
        f"pcbnew.ExportSpecctraDSN(b, {dsn_path!r})"
    )
    result = subprocess.run(
        [python, "-c", script],
        capture_output=True, text=True, timeout=60,
        env=env,
    )
    if result.returncode != 0:
        return f"DSN export failed: {result.stderr.strip()}"
    return None


def import_ses(pcb_path: str, ses_path: str, output_path: str) -> str | None:
    """Import a Specctra SES file into a KiCad PCB via pcbnew subprocess.

    Saves the routed board to output_path. Does not modify the original.
    Returns error message or None on success.
    """
    python, env = find_pcbnew_python()
    if not python:
        return (
            "KiCad Python bindings (pcbnew) not found. "
            "Ensure KiCad is installed. Set KICAD_PYTHON env var if needed."
        )

    script = (
        "import pcbnew; "
        f"b = pcbnew.LoadBoard({pcb_path!r}); "
        f"pcbnew.ImportSpecctraSES(b, {ses_path!r}); "
        f"pcbnew.SaveBoard({output_path!r}, b)"
    )
    result = subprocess.run(
        [python, "-c", script],
        capture_output=True, text=True, timeout=60,
        env=env,
    )
    if result.returncode != 0:
        return f"SES import failed: {result.stderr.strip()}"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_freerouting.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/_freerouting.py tests/test_freerouting.py
git commit -m "feat(autoroute): add pcbnew subprocess helpers for DSN/SES conversion"
```

---

### Task 4: Freerouting subprocess invocation

**Files:**
- Modify: `mcp_server_kicad/_freerouting.py`
- Modify: `tests/test_freerouting.py`

- [ ] **Step 1: Write failing tests for `run_freerouting`**

Add to `tests/test_freerouting.py`:

```python
from mcp_server_kicad._freerouting import run_freerouting


class TestRunFreerouting:
    def test_success(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        ses = tmp_path / "board.ses"
        dsn.touch()
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="Route complete", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(ses),
            )
            assert err is None

    def test_timeout(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        dsn.touch()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="java", timeout=600)):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(tmp_path / "board.ses"),
                timeout=600,
            )
            assert "timeout" in err.lower() or "timed out" in err.lower()

    def test_nonzero_exit(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        dsn.touch()
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Error")
        with patch("subprocess.run", return_value=mock_result):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(tmp_path / "board.ses"),
            )
            assert err is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_freerouting.py::TestRunFreerouting -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `run_freerouting`**

Add to `mcp_server_kicad/_freerouting.py`:

```python
def run_freerouting(
    jar_path: str,
    dsn_path: str,
    ses_path: str,
    max_passes: int = 20,
    num_threads: int = 4,
    timeout: int = 600,
) -> str | None:
    """Run Freerouting autorouter on a DSN file.

    Returns error message or None on success.
    """
    cmd = [
        "java", "-jar", jar_path,
        "-de", dsn_path,
        "-do", ses_path,
        "-mp", str(max_passes),
        "-mt", str(num_threads),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Freerouting timed out after {timeout}s. Try increasing the timeout parameter."

    if result.returncode != 0:
        return f"Freerouting failed (exit {result.returncode}): {result.stderr.strip()}"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_freerouting.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/_freerouting.py tests/test_freerouting.py
git commit -m "feat(autoroute): add Freerouting subprocess invocation"
```

---

## Chunk 2: The `autoroute_pcb` MCP Tool

### Task 5: Add `autoroute_pcb` tool to `pcb.py`

**Files:**
- Modify: `mcp_server_kicad/pcb.py` — add tool between `export_ipc2581` (line 753) and `main()` (line 756)
- Modify: `tests/test_pcb_write_tools.py` — add test class

- [ ] **Step 1: Write failing test for `autoroute_pcb`**

Add to `tests/test_pcb_write_tools.py`:

```python
import json
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

from kiutils.items.common import Position

class TestAutoroutePcb:
    def test_success(self, scratch_pcb, tmp_path):
        """Test full autoroute workflow with mocked external dependencies."""
        # The scratch_pcb has 1 trace and 0 vias.
        # After "routing" we simulate a board with 5 traces and 2 vias.
        routed_path = str(scratch_pcb).replace(".kicad_pcb", "_routed.kicad_pcb")

        def mock_export_dsn(pcb_path, dsn_path):
            Path(dsn_path).touch()  # create fake DSN
            return None

        def mock_import_ses(pcb_path, ses_path, output_path):
            # Copy the original PCB and add traces/vias
            shutil.copy(pcb_path, output_path)
            board = Board.from_file(output_path)
            for i in range(4):
                seg = Segment()
                seg.start = Position(X=50 + i * 10, Y=50)
                seg.end = Position(X=60 + i * 10, Y=50)
                seg.width = 0.25
                seg.layer = "F.Cu"
                seg.net = 1
                seg.tstamp = str(uuid.uuid4())
                board.traceItems.append(seg)
            for i in range(2):
                via = Via()
                via.position = Position(X=70 + i * 10, Y=50)
                via.size = 0.6
                via.drill = 0.3
                via.net = 1
                via.layers = ["F.Cu", "B.Cu"]
                via.tstamp = str(uuid.uuid4())
                board.traceItems.append(via)
            board.to_file()
            return None

        def mock_ensure_jar():
            return "/fake/freerouting.jar", None

        def mock_check_java():
            return None

        def mock_run_freerouting(**kwargs):
            # Create fake SES file
            Path(kwargs.get("ses_path", "/tmp/fake.ses")).touch()
            return None

        with (
            patch("mcp_server_kicad.pcb._check_java", mock_check_java),
            patch("mcp_server_kicad.pcb._ensure_jar", mock_ensure_jar),
            patch("mcp_server_kicad.pcb._export_dsn", mock_export_dsn),
            patch("mcp_server_kicad.pcb._run_freerouting", mock_run_freerouting),
            patch("mcp_server_kicad.pcb._import_ses", mock_import_ses),
        ):
            result = pcb.autoroute_pcb(pcb_path=str(scratch_pcb))
            data = json.loads(result)
            assert "routed_path" in data
            assert data["traces_added"] == 4
            assert data["vias_added"] == 2

    def test_no_java(self, scratch_pcb):
        with patch("mcp_server_kicad.pcb._check_java", return_value="Java not found"):
            result = pcb.autoroute_pcb(pcb_path=str(scratch_pcb))
            data = json.loads(result)
            assert "error" in data
            assert "Java" in data["error"]
```

These imports (`json`, `shutil`, `uuid`, `Path`, `patch`, `Position`) must be added to the top of `test_pcb_write_tools.py` alongside the existing imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_pcb_write_tools.py::TestAutoroutePcb -v`
Expected: FAIL — `AttributeError: module 'mcp_server_kicad.pcb' has no attribute 'autoroute_pcb'`

- [ ] **Step 3: Implement `autoroute_pcb` in `pcb.py`**

Add `import tempfile` to the top of `pcb.py` between `import os` (line 4) and `from pathlib import Path` (line 5), maintaining alphabetical order:

```python
import tempfile
```

Add the following import aliases after the existing imports from `_shared` (around line 30):

```python
from mcp_server_kicad._freerouting import (
    check_java as _check_java,
    ensure_jar as _ensure_jar,
    export_dsn as _export_dsn,
    import_ses as _import_ses,
    run_freerouting as _run_freerouting,
)
```

Add the tool between `export_ipc2581` and `main()` (insert before line 756):

```python
@mcp.tool(annotations=_EXPORT)
def autoroute_pcb(
    pcb_path: str = PCB_PATH,
    max_passes: int = 20,
    num_threads: int = 4,
    timeout: int = 600,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Autoroute PCB traces using the Freerouting autorouter.

    Exports the board to Specctra DSN format, runs Freerouting for automated
    trace routing, and imports the results into a new PCB file. The original
    board is never modified.

    Requires Java 17+ and KiCad's pcbnew Python bindings. On first run,
    the Freerouting JAR is auto-downloaded (~20MB).

    Args:
        pcb_path: Path to .kicad_pcb file
        max_passes: Maximum autorouter optimization passes
        num_threads: Thread count for routing
        timeout: Max seconds to wait for routing (default: 600)
        output_dir: Directory for output files (default: same as PCB)
    """
    # Pre-flight: check Java
    java_err = _check_java()
    if java_err:
        return json.dumps({"error": java_err})

    # Pre-flight: ensure Freerouting JAR
    jar_path, jar_err = _ensure_jar()
    if jar_err:
        return json.dumps({"error": jar_err})

    # Count existing traces/vias for before/after comparison
    board = _load_board(pcb_path)
    traces_before = sum(1 for t in board.traceItems if isinstance(t, Segment))
    vias_before = sum(1 for t in board.traceItems if isinstance(t, Via))

    out_dir = output_dir or str(Path(pcb_path).parent)
    stem = Path(pcb_path).stem
    routed_path = str(Path(out_dir) / f"{stem}_routed.kicad_pcb")

    with tempfile.TemporaryDirectory() as tmp_dir:
        dsn_path = str(Path(tmp_dir) / f"{stem}.dsn")
        ses_path = str(Path(tmp_dir) / f"{stem}.ses")

        # Step 1: Export DSN
        dsn_err = _export_dsn(pcb_path, dsn_path)
        if dsn_err:
            return json.dumps({"error": dsn_err})

        # Step 2: Run Freerouting
        route_err = _run_freerouting(
            jar_path=jar_path,
            dsn_path=dsn_path,
            ses_path=ses_path,
            max_passes=max_passes,
            num_threads=num_threads,
            timeout=timeout,
        )
        if route_err:
            return json.dumps({"error": route_err})

        if not Path(ses_path).exists():
            return json.dumps({"error": "Freerouting did not produce a session file."})

        # Step 3: Import SES into new PCB
        ses_err = _import_ses(pcb_path, ses_path, routed_path)
        if ses_err:
            return json.dumps({"error": ses_err})

    # Count traces/vias in routed board
    routed_board = _load_board(routed_path)
    traces_after = sum(1 for t in routed_board.traceItems if isinstance(t, Segment))
    vias_after = sum(1 for t in routed_board.traceItems if isinstance(t, Via))

    result = {
        "routed_path": str(Path(routed_path).resolve()),
        "traces_added": traces_after - traces_before,
        "vias_added": vias_after - vias_before,
    }

    # Optional DRC
    try:
        drc_out = str(Path(out_dir) / f"{stem}_routed-drc.json")
        _run_cli(
            ["pcb", "drc", "--format", "json", "--severity-all", "--output", drc_out, routed_path],
            check=False,
        )
        with open(drc_out) as f:
            drc = json.load(f)
        violations = []
        for sheet in drc.get("sheets", []):
            violations.extend(sheet.get("violations", []))
        result["drc_violations"] = len(violations)
    except Exception:
        pass  # DRC is optional — kicad-cli may not be available

    return json.dumps(result, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_pcb_write_tools.py::TestAutoroutePcb -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run all PCB tests to check for regressions**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_pcb_write_tools.py tests/test_pcb_read_tools.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat(autoroute): add autoroute_pcb MCP tool"
```

---

## Chunk 3: Test Updates and Skill Integration

### Task 6: Update unified server test counts

**Files:**
- Modify: `tests/test_unified_server.py:37` — change `60` to `61`
- Modify: `tests/test_unified_server.py:50` — change `43` to `44`

- [ ] **Step 1: Update assertions**

In `tests/test_unified_server.py`:

Line 37: change `== 60` to `== 61`
Line 50: change `== 43` to `== 44`

- [ ] **Step 2: Run test to verify**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_unified_server.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_unified_server.py
git commit -m "test: update tool count for autoroute_pcb"
```

---

### Task 7: Update annotation test

**Files:**
- Modify: `tests/test_tool_annotations.py:139-151` — add `"autoroute_pcb"` to `_EXPORT` parametrize list

- [ ] **Step 1: Add `"autoroute_pcb"` to the parametrize list**

In `tests/test_tool_annotations.py`, add `"autoroute_pcb"` to the `test_pcb_export` parametrize list (after `"export_ipc2581"`):

```python
@pytest.mark.parametrize(
    "tool_name",
    [
        "run_drc",
        "export_pcb",
        "export_gerbers",
        "export_3d",
        "export_positions",
        "export_ipc2581",
        "autoroute_pcb",
    ],
)
def test_pcb_export(tool_name):
    assert _get_annotations(pcb, tool_name) == _EXPORT
```

- [ ] **Step 2: Run test to verify**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_tool_annotations.py::test_pcb_export -v`
Expected: PASS (7 parametrized tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_tool_annotations.py
git commit -m "test: add autoroute_pcb to annotation tests"
```

---

### Task 8: Update `pcb-layout` skill

**Files:**
- Modify: `skills/pcb-layout/SKILL.md` — add `autoroute_pcb` to MCP Tools section

- [ ] **Step 1: Add autoroute tool to the Routing section**

In `skills/pcb-layout/SKILL.md`, in the MCP Tools section under **Routing:** (after `add_via`), add:

```markdown
- `autoroute_pcb` — run Freerouting autorouter (requires Java 17+, auto-downloads JAR)
```

- [ ] **Step 2: Commit**

```bash
git add skills/pcb-layout/SKILL.md
git commit -m "docs: add autoroute_pcb to pcb-layout skill"
```

---

### Task 9: Run full test suite and lint

**Files:** None (verification only)

- [ ] **Step 1: Run linting**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && ruff check . && ruff format --check .`
Expected: No errors

- [ ] **Step 2: Run pyright**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && pyright`
Expected: No errors (or only pre-existing warnings)

- [ ] **Step 3: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Fix any issues found, re-run, commit fixes**
