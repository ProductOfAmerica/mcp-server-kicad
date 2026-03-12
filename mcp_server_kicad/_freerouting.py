# mcp_server_kicad/_freerouting.py
"""Freerouting autorouter integration — JAR management, Java checks, subprocess invocation."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

_GITHUB_RELEASES_URL = "https://api.github.com/repos/freerouting/freerouting/releases/latest"

_KICAD_PYTHON_PATHS = [
    "/usr/lib/kicad/lib/python3/dist-packages",
    "/usr/lib/python3/dist-packages",
]

_pcbnew_cache: tuple[str | None, dict | None] | None = None


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


def find_pcbnew_python() -> tuple[str | None, dict | None]:
    """Find a Python interpreter that can import pcbnew.

    Returns (python_path, env_dict) or (None, None).
    Caches result after first successful probe.
    """
    global _pcbnew_cache
    if _pcbnew_cache is not None:
        return _pcbnew_cache

    kicad_python = os.environ.get("KICAD_PYTHON")
    if kicad_python:
        try:
            result = subprocess.run(
                [kicad_python, "-c", "import pcbnew"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                _pcbnew_cache = (kicad_python, None)
                return _pcbnew_cache
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["python3", "-c", "import pcbnew"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            _pcbnew_cache = ("python3", None)
            return _pcbnew_cache
    except Exception:
        pass

    for path in _KICAD_PYTHON_PATHS:
        if not Path(path).is_dir():
            continue
        env = {**os.environ, "PYTHONPATH": path}
        try:
            result = subprocess.run(
                ["python3", "-c", "import pcbnew"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
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
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
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
        detail = result.stderr.strip() or result.stdout.strip()
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
