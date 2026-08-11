# mcp_server_kicad/_freerouting.py
"""Freerouting autorouter integration — JAR management, Java checks, subprocess invocation."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

from mcp_server_kicad._shared import _atomic_write, _kicad_root

_GITHUB_RELEASES_URL = "https://api.github.com/repos/freerouting/freerouting/releases/latest"

_KICAD_PYTHON_PATHS = [
    "/usr/lib/kicad/lib/python3/dist-packages",
    "/usr/lib/python3/dist-packages",
]

_pcbnew_cache: tuple[str | None, dict | None] | None = None
# Wrapped in a 1-tuple so an "unknown" answer is still a cached answer.
_pcbnew_major_cache: tuple[int | None] | None = None


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


def _cache_dir() -> Path:
    """Return the cache directory for Freerouting JAR."""
    return Path.home() / ".local" / "share" / "mcp-server-kicad"


def find_jar() -> str | None:
    """Find the Freerouting JAR. Returns path or None."""
    env_jar = os.environ.get("FREEROUTING_JAR")
    if env_jar and Path(env_jar).is_file():
        return env_jar

    cached = _cache_dir() / "freerouting.jar"
    if cached.is_file():
        return str(cached)

    return None


def _download_jar() -> str:
    """Download the latest Freerouting JAR from GitHub releases. Returns path."""
    req = Request(_GITHUB_RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=30) as resp:
        release = json.loads(resp.read())

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
        _atomic_write(dest, resp.read())

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


def _kicad_python_candidates() -> list[str]:
    """KiCad's own interpreter, which imports pcbnew directly without PYTHONPATH.

    On Windows this is the only one that works out of the box: bare "python3"
    resolves to the Microsoft Store alias in WindowsApps.
    """
    root = _kicad_root()
    if root is None:
        return []
    found = []
    # Windows install and Unix prefix.
    for name in ("python.exe", "python3"):
        p = root / "bin" / name
        if p.is_file():
            found.append(str(p))
    # The macOS bundle has no bin/; it ships a framework whose version
    # component moves (KiCad 10.0.5 bundles 3.9). Every candidate is probed
    # with "import pcbnew" below, so the order among them does not matter.
    found += [
        str(p)
        for p in root.glob("Frameworks/Python.framework/Versions/*/bin/python3")
        if p.is_file()
    ]
    return found


def find_pcbnew_python() -> tuple[str | None, dict | None]:
    """Find a Python interpreter that can import pcbnew.

    Returns (python_path, env_dict) or (None, None).
    Caches result after first successful probe.
    """
    global _pcbnew_cache
    if _pcbnew_cache is not None:
        return _pcbnew_cache

    # uv's managed-python trampoline exports PYTHONHOME, which redirects any
    # child interpreter's module search into uv's tree; KiCad's own python
    # then fails "import pcbnew". Probe and launch without it.
    env = None
    if "PYTHONHOME" in os.environ:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONHOME"}

    kicad_python = os.environ.get("KICAD_PYTHON")
    if kicad_python:
        try:
            result = subprocess.run(
                [kicad_python, "-c", "import pcbnew"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            if result.returncode == 0:
                _pcbnew_cache = (kicad_python, env)
                return _pcbnew_cache
        except Exception:
            pass

    # Try multiple python binaries — when running inside a uvx/venv environment,
    # "python3" may resolve to the venv python which can't import pcbnew.
    # System python (e.g. /usr/bin/python3) typically can, and KiCad's own
    # bundled interpreter always can, so it goes first.
    python_candidates = _kicad_python_candidates() + ["python3"]
    sys_python = "/usr/bin/python3"
    if Path(sys_python).is_file() and sys_python not in python_candidates:
        python_candidates.append(sys_python)

    for py in python_candidates:
        try:
            result = subprocess.run(
                [py, "-c", "import pcbnew"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            if result.returncode == 0:
                _pcbnew_cache = (py, env)
                return _pcbnew_cache
        except Exception:
            pass

    for py in python_candidates:
        for path in _KICAD_PYTHON_PATHS:
            if not Path(path).is_dir():
                continue
            path_env = {**(env or os.environ), "PYTHONPATH": path}
            try:
                result = subprocess.run(
                    [py, "-c", "import pcbnew"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=path_env,
                )
                if result.returncode == 0:
                    _pcbnew_cache = (py, path_env)
                    return _pcbnew_cache
            except Exception:
                pass

    _pcbnew_cache = (None, None)
    return _pcbnew_cache


def pcbnew_major() -> int | None:
    """Major version of the pcbnew bindings the autoroute path would use.

    Returns None whenever it cannot be established: no interpreter, a failing
    subprocess, or output no integer can be read out of. Callers treat None as
    "unknown" and carry on, so this probe never becomes a failure mode of its
    own; the existing pcbnew error paths stay the authority.
    """
    global _pcbnew_major_cache
    if _pcbnew_major_cache is not None:
        return _pcbnew_major_cache[0]

    major = None
    python, env = find_pcbnew_python()
    if python:
        try:
            result = subprocess.run(
                [python, "-c", "import pcbnew; print(pcbnew.Version())"],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            if result.returncode == 0:
                # Version() reads "9.0.8" on KiCad 9.0.8, measured locally.
                # Take the last stdout line so an import banner cannot shadow
                # it, and the first integer in it so a decorated build string
                # ("(10.0.1)", "kicad-10.0.1-rc1") still answers.
                match = re.search(r"\d+", result.stdout.strip().splitlines()[-1])
                if match:
                    major = int(match.group())
        except Exception:
            major = None

    _pcbnew_major_cache = (major,)
    return major


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
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        return f"DSN export failed: {detail}"
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
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        return f"SES import failed: {detail}"
    return None


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
        "java",
        "-jar",
        jar_path,
        "-de",
        dsn_path,
        "-do",
        ses_path,
        "-mp",
        str(max_passes),
        "-mt",
        str(num_threads),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Freerouting timed out after {timeout}s. Try increasing the timeout parameter."

    if result.returncode != 0:
        return f"Freerouting failed (exit {result.returncode}): {result.stderr.strip()}"
    return None
