"""Run a script under KiCad's own Python, with pcbnew, as a judge.

pcbnew is KiCad's board model, so what it computes (a pad's position, a footprint's
courtyard, the board outline) is KiCad's answer by definition. It only imports under
KiCad's own interpreter, which find_pcbnew_python locates; on a machine without one,
skip_reason() says why and a test skips.

run(script, payload) writes ``payload`` as JSON, runs ``script`` with a global
``payload`` (the decoded dict) after wx_app_prelude() (the wxApp pcbnew's API needs
without a GUI), and returns the JSON value the script left in a global ``result``.
Anything that goes wrong there, a missing interpreter, a crash, a timeout, an exception
in the script, no result, raises RuntimeError and never AssertionError: a broken
premise must not pass for the defect a strict xfail expects.

major() asks pcbnew itself, through run(), which major version it is, rather than the
package's own pcbnew_major probe: a judge that took the version from the code it judges
would share that code's mistakes.

pcbnew's Python API is used as measured on 9.0.8. A call whose shape changed in 10.x
belongs inside the script, keyed on pcbnew.Version(), and the KiCad 10 CI legs are where
such a script is checked.
"""

from __future__ import annotations

import functools
import json
import subprocess
import tempfile
from pathlib import Path

from mcp_server_kicad._freerouting import find_pcbnew_python, wx_app_prelude

_NO_PCBNEW = "no Python that imports pcbnew (find_pcbnew_python found none)"


def skip_reason() -> str | None:
    """Why a pcbnew judge cannot run here, or None when it can."""
    python, _ = find_pcbnew_python()
    return None if python else _NO_PCBNEW


_LAUNCHER = (
    "import json; "
    "payload = json.load(open(sys.argv[1], encoding='utf-8')); "
    "scope = {'__name__': '__pcbnew_judge__', 'payload': payload}; "
    "exec(compile(open(sys.argv[3], encoding='utf-8').read(), sys.argv[3], 'exec'), scope); "
    "json.dump(scope['result'], open(sys.argv[2], 'w', encoding='utf-8'))"
)


def run(script: str, payload: dict, *, timeout: float = 300) -> dict:
    """Run ``script`` under pcbnew's Python with ``payload``; return its ``result``."""
    python, env = find_pcbnew_python()
    if python is None:
        raise RuntimeError(_NO_PCBNEW)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        source = Path(work) / "payload.json"
        output = Path(work) / "result.json"
        job = Path(work) / "job.py"
        source.write_text(json.dumps(payload), encoding="utf-8")
        job.write_text(script, encoding="utf-8")
        try:
            proc = subprocess.run(
                [python, "-c", wx_app_prelude() + _LAUNCHER, str(source), str(output), str(job)],
                capture_output=True,
                text=True,
                errors="replace",
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"pcbnew's Python ran past {timeout} s") from exc
        except OSError as exc:
            raise RuntimeError(f"cannot start pcbnew's Python: {exc}") from exc
        if proc.returncode != 0 or not output.is_file():
            raise RuntimeError(
                f"pcbnew's Python failed (rc {proc.returncode}):"
                f" {(proc.stderr or proc.stdout).strip()[-1500:]}"
            )
        try:
            return json.loads(output.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise RuntimeError(f"pcbnew's Python left no JSON result: {exc}") from exc


#: GetMajorMinorVersion() prints KICAD_MAJOR_MINOR_VERSION (common/build_version.cpp:
#: 114-118 at 9.0.8): "9.0" in pcbnew 9.0.8, measured 2026-10-11. Anything but
#: MAJOR.MINOR there is refused rather than guessed at.
_MAJOR_SCRIPT = r"""
import re
import pcbnew
version = pcbnew.GetMajorMinorVersion()
found = re.fullmatch(r"(\d+)\.(\d+)", version)
if found is None:
    raise ValueError(f"pcbnew.GetMajorMinorVersion() is {version!r}, not MAJOR.MINOR")
result = {"major": int(found.group(1))}
"""


@functools.cache
def major() -> int:
    """The major version of the pcbnew bindings run() uses, as pcbnew reports it: the
    MAJOR of GetMajorMinorVersion(), parsed in pcbnew's own interpreter. RuntimeError if
    it cannot be read; one pcbnew run per process, the answer cached."""
    found = run(_MAJOR_SCRIPT, {})
    value = found.get("major") if isinstance(found, dict) else None
    if not isinstance(value, int):
        raise RuntimeError(f"pcbnew's version script left {found!r}")
    return value
