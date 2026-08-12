"""Every path-taking tool answers a path that does not exist the same way.

A path that does not exist is the commonest mistake a caller makes: a model
guesses, or a user typos, or a host ships an empty default. What comes back is
the only thing either of them has to work with.

Measured 2026-08-12, before the openers routed through ``_read_kicad_bytes``:
84 of the 105 path-taking tools answered with an unhandled ``FileNotFoundError``.
An MCP client renders that as a traceback, so a model reading it has nothing to
act on and a user has nothing to fix.

Driven off the live registry rather than a hardcoded list, for the same reason
``test_output_validity`` is: a list is what let 84 tools drift out of contract
in the first place, and a tool added tomorrow inherits this check for free.

Exercised rather than scanned, unlike the write side's
``test_every_write_goes_through_atomic_write``. Behaviour needs no exemption
list for legitimate internal reads and cannot be fooled by a helper reached
through an alias. The point of both is the same: a new tool that opens its file
directly passes every other test in the suite.

The missing path is built under ``tmp_path``, which matters more than it looks.
The first version of this file used the literal "Z:/nonexistent", a path that
is unreachable only on Windows; on Linux it is an ordinary relative path, so
``create_schematic`` created it inside the checkout and the next ten tools then
found a real file waiting for them. Twelve cases failed in CI and passed
locally. A path that is missing on every platform has to be built, not written
down. The unreachable-*root* case that ``_ensure_dir`` guards is a different
failure and is tested in ``test_shared_helpers.TestEnsureDir``.
"""

from __future__ import annotations

import contextlib
import inspect
import subprocess
import typing
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import footprint, pcb, project, schematic, symbol

_MODULES = (schematic, pcb, project, symbol, footprint)

_SUFFIX = {
    "schematic_path": ".kicad_sch",
    "pcb_path": ".kicad_pcb",
    "symbol_lib_path": ".kicad_sym",
    "footprint_path": ".kicad_mod",
    "project_path": ".kicad_pro",
}

#: Tools for which a path that does not exist is correct input, not a mistake.
#: Creating the file is the whole job, and these refuse the *opposite* case: a
#: path that already exists.
_CREATES_ITS_FILE = {
    "create_schematic": "writes a new schematic at the path it is given",
    "create_symbol_library": "writes a new symbol library at the path it is given",
}

#: Tools that return before resolving any path.
_NO_PATH_REACHED = {
    # An empty pin list means there is no work, so it returns before opening the
    # schematic. No path is resolved, so there is nothing to be wrong about;
    # passing pins would reach the opener like everything else.
    "wire_pins_to_net": "an empty pins list returns before the file is opened",
}

_EXEMPT = _CREATES_ITS_FILE | _NO_PATH_REACHED


def _placeholder(tmp_path: Path, name: str, annotation: object) -> object:
    """A type-appropriate value for a required parameter.

    Required arguments are filled so a failure is attributable to the path and
    not to a dummy of the wrong type landing in a different validation branch.
    Every directory-ish parameter points inside *tmp_path*, so a tool that
    writes despite the missing path writes somewhere disposable.
    """
    text = str(annotation)
    if name.endswith("_path") or name in ("directory", "output_dir", "pretty_dir"):
        return str(tmp_path / "absent")
    if "Literal" in text:
        args = typing.get_args(annotation)
        return args[0] if args else ""
    if "list" in text:
        return []
    if "bool" in text:
        return True
    if "int" in text or "float" in text:
        return 0
    return "X"


def _path_taking_tools():
    for mod in _MODULES:
        for name, tool in mod.mcp._tool_manager._tools.items():
            targets = [p for p in inspect.signature(tool.fn).parameters if p.endswith("_path")]
            if targets:
                yield name, tool.fn, targets[0]


#: Plain tuples, with the pytest.param wrapper derived from them. Both tests
#: below need the same sweep, and unpacking a param's .values back out is not
#: typed well enough for pyright to see a callable.
_SWEEP = list(_path_taking_tools())
_TOOLS = [pytest.param(name, fn, target, id=name) for name, fn, target in _SWEEP]


def _missing_path_kwargs(fn, target: str, tmp_path: Path) -> dict:
    """Required arguments filled, and *target* pointed at a file that is not there."""
    kwargs = {}
    for pname, p in inspect.signature(fn).parameters.items():
        if p.default is inspect.Parameter.empty or pname == target:
            kwargs[pname] = _placeholder(tmp_path, pname, p.annotation)
    kwargs[target] = str(tmp_path / f"absent{_SUFFIX.get(target, '')}")
    return kwargs


def test_the_registry_is_actually_populated():
    """Guards the guard.

    Every case below is vacuous if the sweep finds nothing, and an enumeration
    reaching into a private ``_tool_manager`` is exactly the kind of thing that
    silently returns empty after an SDK bump.
    """
    assert len(_SWEEP) >= 100, f"only {len(_SWEEP)} path-taking tools found"


@pytest.mark.parametrize(("name", "fn", "target"), _TOOLS)
def test_a_missing_path_is_answered_in_contract(name, fn, target, tmp_path):
    kwargs = _missing_path_kwargs(fn, target, tmp_path)

    if name in _EXEMPT:
        # Checked in both directions on purpose. An exemption that stops being
        # true is a tool that quietly changed contract, and a mute list would
        # hide exactly that.
        fn(**kwargs)
        return

    try:
        fn(**kwargs)
    except ToolError:
        return
    except Exception as exc:  # noqa: BLE001 - classifying the leak is the point
        pytest.fail(
            f"{name} leaked {type(exc).__name__} on a missing {target}: {exc}\n"
            "Read the file through _shared._read_kicad_bytes, and create output"
            " directories through _shared._ensure_dir, so the caller gets a"
            " message naming the problem instead of a traceback."
        )
    pytest.fail(
        f"{name} accepted a path that does not exist. Either it is missing the"
        f" guard, or it never resolves {target} and belongs in one of the two"
        " exemption maps with its reason."
    )


def test_an_unset_default_says_so():
    """The empty path is a different mistake and gets a different message.

    An unset default is not a typo. It is what an unconfigured host produces,
    and a user hit exactly this: every schematic tool defaulted to "" because
    KICAD_SCH_PATH was never set. Pointing at the path would be useless, since
    there is no path; this points at the host configuration, where the fix is.

    Left to the contract, "" would also be wrong in a second way. On Windows
    Path("").read_bytes() raises PermissionError, not FileNotFoundError,
    because an empty path reads the working directory as a file, so the caller
    got a permissions error naming a directory they never mentioned.
    """
    with pytest.raises(ToolError, match="none is configured"):
        schematic.list_schematic_components(schematic_path="")


def test_a_missing_path_costs_no_subprocess(tmp_path, monkeypatch):
    """The 20 CLI-backed tools check the path before they spawn anything.

    They reached a ToolError either way, because _run_cli raises when kicad-cli
    fails, so the contract sweep above passes with or without this. What
    differed was the message and the cost: the caller got kicad-cli's
    diagnostic about a file it could not open, one process later.

    Measured 2026-08-12 by counting subprocess.run calls across the whole sweep:
    20 before, 0 after. Nineteen were kicad-cli; the twentieth was java, because
    autoroute_pcb started the freerouting JVM before looking at the board.
    """
    spawns: list[str] = []
    real = subprocess.run

    def counting_run(*args, **kwargs):
        spawns.append(str(args[0])[:80] if args else "")
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting_run)

    for name, fn, target in _SWEEP:
        if name in _EXEMPT:
            continue
        with contextlib.suppress(ToolError):
            fn(**_missing_path_kwargs(fn, target, tmp_path))

    assert spawns == [], (
        f"{len(spawns)} subprocess(es) spawned for paths that do not exist:\n  "
        + "\n  ".join(spawns)
        + "\nCall _require_kicad_path before the spawn."
    )
