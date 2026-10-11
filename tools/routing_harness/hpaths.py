"""Paths, environment and the import bootstrap of the routing harness.

Every script under tools/routing_harness imports this module first: entry scripts through the
preamble below, library modules with a bare `import hpaths` (an entry script has already put this
directory on sys.path). Importing it, once per process:
  - turns bytecode writing off (the harness writes nothing beside its code);
  - puts the repository under test (TARGET), core/, protos/ and adapters/ at the front of
    sys.path, in that order, and TARGET/tests at the end (it supplies conftest; appending keeps a
    test module from shadowing a harness module);
  - exports the resolved ROUTING_HARNESS_* values to os.environ so child processes agree;
  - installs the frozen 54160c1 helpers (core/compat54.py, adds one attribute only if absent);
  - raises unless mcp_server_kicad resolves under TARGET (assert_target).

Preamble of an entry script (anything run as `python <file>`):

    import sys
    from pathlib import Path

    sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
    sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
    import hpaths  # noqa: E402

    hpaths.bootstrap(__file__)

(The bytecode line comes first because hpaths.py is the one module that cannot switch the flag off
before it is compiled; without it, `python <file>` caches hpaths.py's bytecode in this directory.)

Environment, all optional:
  PT_REPO                  checkout under test (needs mcp_server_kicad/ and tests/conftest.py, for
                           example `git archive <sha> mcp_server_kicad tests`); default: the
                           checkout that holds this harness
  ROUTING_HARNESS_WORK     work directory (default: work/ beside this file; git-ignored)
  ROUTING_HARNESS_RUN      run name, [A-Za-z0-9._-]+ (default: default); one run is one directory
                           under WORK/runs holding fixtures, records and run.json
  ROUTING_HARNESS_DESIGNS  comma list of design names (core/registry.py), default X
  ROUTING_HARNESS_FU2      1 restores follow-up 2's two demo branches (see README)
  KICAD_CLI_PATH           kicad-cli to use (the package's own resolution otherwise)
  KICAD_DEMOS_DIR          KiCad's demos directory, when it is not under the install root
  KICAD_SYMBOL_DIR         KiCad's symbol libraries, when they are not under the install root

`python hpaths.py` prints every resolved name and the guard result.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

_RUN_RE = re.compile(r"[A-Za-z0-9._-]+")

HARNESS = Path(__file__).resolve().parent
try:
    REPO = HARNESS.parents[1]
except IndexError:  # a harness copied to the top of a drive
    REPO = HARNESS
if not ((REPO / "mcp_server_kicad").is_dir() and (REPO / "pyproject.toml").is_file()):
    raise RuntimeError(
        f"{HARNESS} is not at tools/routing_harness of an mcp-server-kicad checkout: "
        f"{REPO} holds no mcp_server_kicad/ and pyproject.toml"
    )

_pt_repo = os.environ.get("PT_REPO", "")
TARGET = Path(_pt_repo).resolve() if _pt_repo else REPO
if not ((TARGET / "mcp_server_kicad").is_dir() and (TARGET / "tests" / "conftest.py").is_file()):
    raise RuntimeError(
        f"{TARGET} cannot be the repository under test: it needs mcp_server_kicad/ and "
        "tests/conftest.py (core/harness.py imports conftest). To test another commit, export "
        "it with `git archive <sha> mcp_server_kicad tests` and point PT_REPO at the result."
    )

CORE = HARNESS / "core"
PROTOS = HARNESS / "protos"
ADAPTERS = HARNESS / "adapters"
SUITES = HARNESS / "suites"
ANALYSIS = HARNESS / "analysis"
CASEIDX = HARNESS / "caseidx"
BASELINES = HARNESS / "baselines"
REPORTS = HARNESS / "reports"

PYTHON = sys.executable


def _derive() -> None:
    """(Re)compute the names that come from the environment."""
    global WORK, RUN, RUN_DIR, FIXTURES, DESIGNS, FU2
    work = Path(os.environ.get("ROUTING_HARNESS_WORK") or HARNESS / "work").resolve()
    run = os.environ.get("ROUTING_HARNESS_RUN") or "default"
    if not _RUN_RE.fullmatch(run) or set(run) <= {"."}:
        raise RuntimeError(f"ROUTING_HARNESS_RUN={run!r}: a run name is [A-Za-z0-9._-]+ and not only dots")
    names = [n.strip() for n in (os.environ.get("ROUTING_HARNESS_DESIGNS") or "X").split(",")]
    WORK, RUN = work, run
    RUN_DIR = WORK / "runs" / RUN
    FIXTURES = RUN_DIR / "fixtures"
    DESIGNS = tuple(dict.fromkeys(n for n in names if n)) or ("X",)
    FU2 = os.environ.get("ROUTING_HARNESS_FU2") == "1"


def _export() -> None:
    """Make children (and anything reading os.environ) agree with this process."""
    os.environ["ROUTING_HARNESS_WORK"] = str(WORK)
    os.environ["ROUTING_HARNESS_RUN"] = RUN
    os.environ["ROUTING_HARNESS_DESIGNS"] = ",".join(DESIGNS)
    os.environ["ROUTING_HARNESS_FU2"] = "1" if FU2 else "0"


_derive()


def configure(run=None, designs=None, fu2=None, work=None) -> None:
    """Change the run, the designs, the FU2 switch or the work directory of this process.

    For the three entry scripts that take these as command-line flags (run.py, prepare.py,
    smoke.py): they call it right after `import hpaths`, before anything else reads the names,
    and the environment follows so their children agree. Nothing else calls it.
    """
    keys = ("ROUTING_HARNESS_RUN", "ROUTING_HARNESS_DESIGNS", "ROUTING_HARNESS_FU2", "ROUTING_HARNESS_WORK")
    before = {k: os.environ.get(k) for k in keys}
    if run is not None:
        os.environ["ROUTING_HARNESS_RUN"] = str(run)
    if designs is not None:
        os.environ["ROUTING_HARNESS_DESIGNS"] = designs if isinstance(designs, str) else ",".join(designs)
    if fu2 is not None:
        os.environ["ROUTING_HARNESS_FU2"] = "1" if fu2 else "0"
    if work is not None:
        os.environ["ROUTING_HARNESS_WORK"] = str(work)
    try:
        _derive()
    except RuntimeError:
        for k, v in before.items():  # a refused value leaves the environment as it was
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        raise
    _export()


def _key(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _front(paths) -> None:
    """Put paths at the front of sys.path in the given order, dropping earlier duplicates."""
    paths = [str(p) for p in paths]
    keys = {_key(p) for p in paths}
    sys.path[:] = paths + [p for p in sys.path if _key(p) not in keys]


def _append(path) -> None:
    if _key(path) not in {_key(p) for p in sys.path}:
        sys.path.append(str(path))


def suite_out(name: str) -> Path:
    """RUN_DIR/<name>, created: where one suite or analysis writes its outputs."""
    d = RUN_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_dir(arg) -> Path:
    """The directory of a run: an explicit path when `arg` is absolute or holds a separator, else
    WORK/runs/<arg>. Used by gate.py and the analysis scripts in place of a scratch rerun name."""
    arg = str(arg)
    if os.path.isabs(arg) or "/" in arg or os.sep in arg:
        return Path(arg).resolve()
    return WORK / "runs" / arg


def child_env(**extra) -> dict:
    """os.environ for a child process: bytecode off, UTF-8 output, and this process's
    ROUTING_HARNESS_* values (plus PT_REPO when it is set), then `extra`. Never PYTHONPATH: every
    child bootstraps itself."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["ROUTING_HARNESS_WORK"] = str(WORK)
    env["ROUTING_HARNESS_RUN"] = RUN
    env["ROUTING_HARNESS_DESIGNS"] = ",".join(DESIGNS)
    env["ROUTING_HARNESS_FU2"] = "1" if FU2 else "0"
    if os.environ.get("PT_REPO"):
        env["PT_REPO"] = str(TARGET)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def assert_target(module_file=None) -> None:
    """Raise unless mcp_server_kicad resolved under TARGET (a different checkout's editable
    install, or a stale PYTHONPATH, would otherwise be measured instead). With no argument it
    imports mcp_server_kicad._cst and checks that file."""
    if module_file is None:
        from mcp_server_kicad import _cst

        module_file = _cst.__file__
    if not module_file:
        raise RuntimeError(f"mcp_server_kicad has no file location, not under {TARGET}")
    got = Path(_key(Path(module_file).resolve()))
    if not got.is_relative_to(Path(_key(TARGET))):
        raise RuntimeError(f"mcp_server_kicad resolved to {module_file}, not under {TARGET}")


def kicad_cli_info() -> dict:
    """{"path", "version"} of the kicad-cli the package resolves; version is None when it cannot be run."""
    from mcp_server_kicad import _shared

    path = _shared._find_kicad_cli()
    version = None
    if path:
        try:
            version = (_shared._run_cli(["version"], check=False).stdout or "").strip() or None
        except Exception:  # noqa: BLE001  (a crash or a timeout is reported as an unknown version)
            pass
    return {"path": path, "version": version}


def _suite_root(directory: Path):
    for p in (directory, *directory.parents):
        if p.parent == SUITES:
            return p
    return None


def bootstrap(file, suites=()) -> None:
    """Make a script's own imports work: the directory of `file`, the suites/<name> root that
    contains it and that root's shims/ (when it exists), then each SUITES/<s> in `suites` (for
    analysis scripts that import a suite's modules), all at the front of sys.path. Idempotent."""
    here = Path(file).resolve().parent
    wanted = [here]
    root = _suite_root(here)
    if root is not None:
        wanted.append(root)
        if (root / "shims").is_dir():
            wanted.append(root / "shims")
    wanted.extend(SUITES / s for s in suites)
    _front(wanted)


def _resolve_root() -> Path:
    from mcp_server_kicad._shared import _kicad_root

    root = _kicad_root()
    if root is None:
        raise RuntimeError(
            "kicad-cli was not found. Set KICAD_CLI_PATH to its full path (or put it on PATH): the "
            "harness takes KiCad's demos and symbol libraries from the install it belongs to."
        )
    return Path(root)


def _first_existing(base: Path, subs, what: str, override: str) -> Path:
    for sub in subs:
        if (base / sub).is_dir():
            return base / sub
    raise RuntimeError(
        f"KiCad's {what} were not found under {base} (looked for {', '.join(subs)}). "
        f"Set {override} to the directory."
    )


def _resolve_share() -> Path:
    return _first_existing(_resolve_root(), ("share/kicad", "SharedSupport"), "shared data", "KICAD_CLI_PATH")


def _resolve_demos() -> Path:
    env = os.environ.get("KICAD_DEMOS_DIR")
    if env:
        if not Path(env).is_dir():
            raise RuntimeError(f"KICAD_DEMOS_DIR={env} is not a directory")
        return Path(env).resolve()
    return _first_existing(
        _resolve_root(), ("share/kicad/demos", "SharedSupport/demos"), "demos", "KICAD_DEMOS_DIR"
    )


def _resolve_symbols() -> Path:
    env = os.environ.get("KICAD_SYMBOL_DIR")
    if env:
        if not Path(env).is_dir():
            raise RuntimeError(f"KICAD_SYMBOL_DIR={env} is not a directory")
        return Path(env).resolve()
    return _first_existing(
        _resolve_root(),
        ("share/kicad/symbols", "SharedSupport/symbols"),
        "symbol libraries",
        "KICAD_SYMBOL_DIR",
    )


# These import the package, so they resolve on first use, after the sys.path setup below.
_LAZY = {
    "KICAD_ROOT": _resolve_root,
    "KICAD_SHARE": _resolve_share,
    "KICAD_DEMOS": _resolve_demos,
    "KICAD_SYMBOLS": _resolve_symbols,
}


def __getattr__(name):
    if name in _LAZY:
        value = _LAZY[name]()
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _setup() -> None:
    _front([TARGET, CORE, PROTOS, ADAPTERS])
    _append(TARGET / "tests")
    _export()
    if _pt_repo:
        os.environ["PT_REPO"] = str(TARGET)
    import compat54

    compat54.install()
    assert_target()


_setup()


if __name__ == "__main__":
    import gui_reading

    for _name in ("HARNESS", "REPO", "TARGET", "CORE", "PROTOS", "ADAPTERS", "SUITES", "ANALYSIS",
                  "CASEIDX", "BASELINES", "REPORTS", "WORK", "RUN", "RUN_DIR", "FIXTURES", "PYTHON",
                  "DESIGNS", "FU2", "KICAD_ROOT", "KICAD_SHARE", "KICAD_DEMOS", "KICAD_SYMBOLS"):
        try:
            _value = globals()[_name] if _name in globals() else __getattr__(_name)
        except RuntimeError as _e:
            print(f"{_name} = <unresolved: {_e}>")
            continue
        print(f"{_name} = {_value}")
    try:
        assert_target()
        print("guard: mcp_server_kicad resolves under TARGET")
    except RuntimeError as _e:
        print(f"guard: FAILED: {_e}")
        sys.exit(1)
    print(f"gui reading: {'available' if gui_reading.AVAILABLE else 'not available'}")
    if not gui_reading.AVAILABLE:
        print(f"  {gui_reading.REASON}")
