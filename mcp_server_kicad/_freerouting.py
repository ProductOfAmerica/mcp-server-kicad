# mcp_server_kicad/_freerouting.py
"""Freerouting autorouter integration — JAR management, Java checks, subprocess invocation."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import urllib.request
import zipfile
from pathlib import Path

from mcp_server_kicad._shared import _atomic_write, _find_on_path, _kicad_root, _run_pcbnew

#: The one Freerouting build autoroute_pcb downloads when FREEROUTING_JAR names
#: no jar: an exact release asset, never whichever release is newest, so the
#: bytes that run change only when this pin does. The digest is the one GitHub
#: publishes for the asset, matched by hashing a download of it.
_FREEROUTING_VERSION = "2.5.0"
_FREEROUTING_URL = (
    "https://github.com/freerouting/freerouting/releases/download/"
    f"v{_FREEROUTING_VERSION}/freerouting-{_FREEROUTING_VERSION}.jar"
)
_FREEROUTING_SHA256 = "f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7"

_KICAD_PYTHON_PATHS = [
    "/usr/lib/kicad/lib/python3/dist-packages",
    "/usr/lib/python3/dist-packages",
]

_pcbnew_cache: tuple[str | None, dict | None] | None = None
# Wrapped in a 1-tuple so an "unknown" answer is still a cached answer.
_pcbnew_major_cache: tuple[int | None] | None = None


#: What the pinned jar needs: its classes under app/freerouting/ are all class
#: file major 69. The fallback when a jar cannot be read; jar_java_requirement
#: is the authority when it answers, as it does for the pinned jar.
_JAVA_FLOOR = 25

#: Class file major 45 is Java 1.1, and it has incremented by one per release
#: since, so Java N is 44 + N. Java 17 is 61, Java 25 is 69.
_CLASS_MAJOR_BASE = 44

#: Leads every inline script handed to a child interpreter, ahead of any import.
#: ``python -c`` puts "" (the current directory) first on sys.path, and these
#: children inherit the server's working directory, so a pcbnew.py or wx.py
#: there would be imported in place of KiCad's own. sys is built in, so
#: importing it searches nothing. Not -P or PYTHONSAFEPATH, which arrived in
#: Python 3.11 while KiCad 10's macOS bundle ships 3.9; and not -I, which also
#: drops PYTHONPATH, the thing the fallback in find_pcbnew_python sets.
_NO_CWD_IMPORTS = "import sys; sys.path[:] = [p for p in sys.path if p]; "


def jar_java_requirement(jar_path: str) -> int | None:
    """Java major version *jar_path* needs, read from its own class files.

    Asking the jar rather than trusting a constant, because FREEROUTING_JAR can
    name any Freerouting build, and only its class files say what it needs. The
    download floated as well until it was pinned: it fetched whatever release
    was newest, and on 2026-08-14 the cached freerouting.jar (build 2026-08-07)
    was class file major 69, meaning Java 25, against a check that was passing
    anything 17 or above. Every machine between the two passed the preflight and
    then died in the router.

    Entries under META-INF/versions/ are skipped: a multi-release jar carries
    higher-versioned copies for runtimes that can use them, and requiring the
    highest would refuse a Java the jar runs on perfectly well.

    Returns None when the jar cannot be read, and callers fall back rather than
    failing, so this probe cannot become a failure mode of its own.
    """
    try:
        with zipfile.ZipFile(jar_path) as zf:
            majors = []
            for name in zf.namelist():
                if not name.endswith(".class") or name.startswith("META-INF/versions/"):
                    continue
                head = zf.read(name)[:8]
                if len(head) >= 8 and head[:4] == b"\xca\xfe\xba\xbe":
                    majors.append(int.from_bytes(head[6:8], "big"))
                if len(majors) >= 40:  # a sample; a jar is not built per-class
                    break
    except Exception:
        return None
    if not majors:
        return None
    return max(majors) - _CLASS_MAJOR_BASE


def check_java(jar_path: str | None = None, *, java: str | None) -> str | None:
    """Check the Java at *java* is new enough for *jar_path*. Message, or None.

    *java* is the absolute path the caller looked up once and hands to
    run_freerouting as well, so the binary checked here is the binary that
    routes. None means the lookup found no java at all. Keyword-only, so a call
    written against the old signature fails loudly instead of passing the jar
    off as the binary.

    The requirement comes from the jar when one is given and readable, and from
    _JAVA_FLOOR otherwise.
    """
    needed = (jar_java_requirement(jar_path) if jar_path else None) or _JAVA_FLOOR
    how = (
        "Install a JRE of at least that version: "
        "https://adoptium.net, `brew install openjdk`, or `apt install default-jre`."
    )
    missing = f"Java runtime not found. Autorouting needs Java {needed}+. {how}"
    if java is None:
        return missing
    try:
        result = subprocess.run(
            [java, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        # Gone between the lookup and the launch.
        return missing
    except subprocess.TimeoutExpired:
        # Uncaught until 2026-08-14, so a wedged java propagated a raw exception.
        return f"`java -version` did not answer within 10s. Autorouting needs Java {needed}+."

    version_output = result.stderr + result.stdout
    match = re.search(r'"(\d+)[\.\d]*"', version_output)
    if not match:
        detail = version_output.strip() or f"exit code {result.returncode}"
        return f"Could not read the Java version from: {detail}"

    major = int(match.group(1))
    if major < needed:
        source = "the freerouting jar reports it needs" if jar_path else "autorouting needs"
        return f"Java {major} found but {source} Java {needed}+. {how}"
    return None


def _cache_dir() -> Path:
    """Return the cache directory for Freerouting JAR."""
    return Path.home() / ".local" / "share" / "mcp-server-kicad"


def _cached_jar() -> Path:
    """Where the pinned jar is cached.

    The version is in the name, so two installed releases of this package that
    pin different builds keep one jar each instead of replacing each other's on
    every call, and the unpinned freerouting.jar that releases before the pin
    cached here is never read.
    """
    return _cache_dir() / f"freerouting-{_FREEROUTING_VERSION}.jar"


def _is_pinned(path: Path) -> bool:
    """Whether *path* holds exactly the pinned jar's bytes."""
    try:
        with open(path, "rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest() == _FREEROUTING_SHA256
    except OSError:
        return False


def find_jar() -> str | None:
    """Find the Freerouting JAR. Returns path or None.

    FREEROUTING_JAR is the user's own jar, used as given and never hash-checked.
    A relative one is ignored, as a relative KICAD_CLI_PATH is: is_file()
    resolved it against the working directory and java then loaded whatever jar
    of that name sat in the directory the server started in.

    The cached jar is hashed again on every call rather than trusted for its
    name, because a name cannot tell the pinned jar from one altered after it
    was saved. Hashing the pinned jar took 0.12 to 0.15 s on a desktop
    (measured 2026-10-19).
    """
    env_jar = os.environ.get("FREEROUTING_JAR")
    if env_jar and os.path.isabs(env_jar) and Path(env_jar).is_file():
        return env_jar

    cached = _cached_jar()
    if _is_pinned(cached):
        return str(cached)
    return None


def _download_jar() -> str:
    """Download the pinned Freerouting JAR into the cache. Returns its path.

    The whole download is hashed before anything is written, and it lands
    through _atomic_write, so the cache never holds a partial or unchecked jar.
    """
    # Through the module, never a name imported from it: the suite's _no_network
    # guard patches urllib.request.urlopen, which a name bound at import never sees.
    with urllib.request.urlopen(_FREEROUTING_URL, timeout=120) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != _FREEROUTING_SHA256:
        raise RuntimeError(
            f"the download's SHA-256 is {digest}, not the pinned {_FREEROUTING_SHA256},"
            " so it was neither saved nor run"
        )
    dest = _cached_jar()
    dest.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(dest, data)
    return str(dest)


def ensure_jar() -> tuple[str | None, str | None]:
    """Ensure the Freerouting JAR is available. Downloads if needed.

    Returns (jar_path, error_message). One of them is always None.
    """
    jar = find_jar()
    if jar:
        return jar, None

    try:
        return _download_jar(), None
    except Exception as exc:
        return None, (
            f"Failed to download Freerouting {_FREEROUTING_VERSION}: {exc}. "
            f"Download {_FREEROUTING_URL} yourself, check that its SHA-256 is "
            f"{_FREEROUTING_SHA256}, and set the FREEROUTING_JAR environment variable "
            "to its absolute path."
        )


def _kicad_python_candidates() -> list[str]:
    """KiCad's own interpreter, which imports pcbnew directly without PYTHONPATH.

    On Windows this is the only one that works out of the box: python3 on PATH
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


def wx_app_prelude() -> str:
    """Statements to prepend to any inline pcbnew script, ending in "; ".

    Every pcbnew subprocess this package spawns runs a GUI library with no GUI
    and, until 2026-08-13, with no wxApp either. Anything reaching
    wxStandardPaths::Get() then asserts, and the handler that runs is the raw
    C++ one, because wxPython installs its own through wxApp. On Windows that
    is a modal "wxWidgets Debug Alert" on the user's desktop which this
    subprocess has nobody to dismiss, so the tool call blocks to its timeout; on
    macOS it kills the process, which is how it was first seen.

    Single-sourced through _netlist_import so the call sites cannot drift.
    That module imports only the standard library, so KiCad's interpreter can
    load it, and it is already on disk next to this one.

    It opens with _NO_CWD_IMPORTS. Inserting the package directory alone left
    the working directory one slot behind it, still ahead of everything the
    interpreter ships with.
    """
    pkg_dir = str(Path(__file__).parent)
    return (
        _NO_CWD_IMPORTS + f"sys.path.insert(0, {pkg_dir!r}); "
        "import _netlist_import as _ni; _ni._ensure_wx_app(); "
    )


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
    # KICAD_CLI_PATH's rule, so nothing launched here is relative to the
    # working directory. Handed to subprocess unchanged, a bare name was
    # searched for there before PATH on Windows, and "sub/python3" ran from it
    # everywhere. A bare name goes through _find_on_path, which appends the
    # ".exe" itself; any other relative value is ignored and discovery goes on.
    if kicad_python and not os.path.isabs(kicad_python):
        if os.path.basename(kicad_python) == kicad_python:
            if os.name == "nt" and kicad_python.lower().endswith(".exe"):
                kicad_python = kicad_python[: -len(".exe")]
            kicad_python = _find_on_path(kicad_python)
        else:
            kicad_python = None
    if kicad_python:
        try:
            result = subprocess.run(
                [kicad_python, "-c", _NO_CWD_IMPORTS + "import pcbnew"],
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

    # Try several interpreters. Inside a uvx or venv environment, python3 on
    # PATH may be the venv's, which cannot import pcbnew. System python (e.g.
    # /usr/bin/python3) typically can, and KiCad's own bundled interpreter
    # always can, so it goes first. python3 goes through _find_on_path: handed
    # to subprocess by bare name, it was searched for in the working directory
    # before PATH on Windows.
    python_candidates = _kicad_python_candidates()
    on_path = _find_on_path("python3")
    if on_path:
        python_candidates.append(on_path)
    sys_python = "/usr/bin/python3"
    if Path(sys_python).is_file() and sys_python not in python_candidates:
        python_candidates.append(sys_python)

    for py in python_candidates:
        try:
            result = subprocess.run(
                [py, "-c", _NO_CWD_IMPORTS + "import pcbnew"],
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
                    [py, "-c", _NO_CWD_IMPORTS + "import pcbnew"],
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
                [python, "-c", _NO_CWD_IMPORTS + "import pcbnew; print(pcbnew.Version())"],
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
        wx_app_prelude() + "import pcbnew; "
        f"b = pcbnew.LoadBoard({pcb_path!r}); "
        f"pcbnew.ExportSpecctraDSN(b, {dsn_path!r})"
    )
    result = _run_pcbnew([python, "-c", script], what="exporting the DSN", timeout=60, env=env)
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
        wx_app_prelude() + "import pcbnew; "
        f"b = pcbnew.LoadBoard({pcb_path!r}); "
        f"pcbnew.ImportSpecctraSES(b, {ses_path!r}); "
        f"pcbnew.SaveBoard({output_path!r}, b)"
    )
    result = _run_pcbnew(
        [python, "-c", script], what="importing the SES route", timeout=60, env=env
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
    num_threads: int = 1,
    timeout: int = 600,
    *,
    java: str,
) -> str | None:
    """Run Freerouting autorouter on a DSN file.

    *java* is the binary check_java approved, by absolute path.

    Returns error message or None on success.
    """
    cmd = [
        java,
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
