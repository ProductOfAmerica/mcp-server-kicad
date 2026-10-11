"""Launch the six batches of one pressure-test run (what launch3.ps1 did), detached and resumable.

usage: run.py [--run NAME] [--designs LIST] [--fu2] [--only BATCHES] [--wait] [--skip-prepare]
              [--allow-drift]

  --run NAME        the run: everything goes under WORK/runs/NAME (default: ROUTING_HARNESS_RUN,
                    else "default"). Re-running a run resumes it: each batch skips finished work.
  --designs LIST    comma list of designs (core/registry.py: X, Hb2, Hb, H, M2, E); default X
  --fu2             restore follow-up 2's two demo branches (the recoveries; see README)
  --only BATCHES    comma list of batches to launch: demo, probeA, probeB, probeC, layouts, llm
  --wait            wait for the batches and exit non-zero if any of them did (default: detach)
  --skip-prepare    do not run prepare.py first (the fixtures must already be there)
  --allow-drift     launch although prepare.py found a fixture that differs from the baselines'
                    (recorded in run.json)

It writes RUN_DIR/run.json (the run, designs, FU2, the checkout under test and its commit, kicad-cli
and its version, the demos, whether the GUI reading is available, start time, the prepare result and
the batches' pids), runs prepare.py, and starts the batches: demo run_sweeps.py --workers=5, probeB
driver.py 3, probeC runq.py (RUNQ_MAXP=3), layouts runner.py --workers 3 --resume, probeA
core/seqrun.py suites/probeA/jobs_probeA.json 1, llm core/seqrun.py
suites/llm-workflow/jobs_llm.json 2. Each batch's output goes to RUN_DIR/meta/<batch>.<stamp>.out and
.err. One design takes about two hours on 16 logical CPUs. To stop a run, kill whole process trees
(a batch's children keep running when only the batch is killed). Then gate it: core/gate.py <run>
<design>.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import argparse  # noqa: E402
import datetime  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

import gui_reading  # noqa: E402
import registry  # noqa: E402

# (cwd under tools/routing_harness, script, arguments, extra environment), in launch3.ps1's order.
BATCHES = {
    "demo": ("suites/demo", "run_sweeps.py", ["--workers=5"], {}),
    "probeB": ("suites/probeB", "driver.py", ["3"], {}),
    "probeC": ("suites/probeC", "runq.py", [], {"RUNQ_MAXP": "3"}),
    "layouts": ("suites/layouts_stock", "runner.py", ["--workers", "3", "--resume"], {}),
    "probeA": (".", "core/seqrun.py", ["suites/probeA/jobs_probeA.json", "1"], {}),
    "llm": (".", "core/seqrun.py", ["suites/llm-workflow/jobs_llm.json", "2"], {}),
}
ALIASES = {"layouts_stock": "layouts", "llm-workflow": "llm"}


def git_head(path: Path):
    """The commit of `path` when it is itself a git checkout, else None (an export of a commit made
    with git archive sits inside this repository's work tree and must not report its HEAD)."""
    def git(*args):
        try:
            r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        except OSError:
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    if not top or os.path.normcase(str(Path(top).resolve())) != os.path.normcase(str(path.resolve())):
        return None
    return git("rev-parse", "HEAD")


def alive(pid: int) -> bool:
    """Is a python process with this pid running? (os.kill(pid, 0) would kill it on Windows.)"""
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True,
                           text=True, errors="replace")
        return any(line.split(",")[1:2] == [f'"{pid}"'] and "python" in line.lower()
                   for line in r.stdout.splitlines())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def run_info() -> dict:
    """The description of the current run configuration that run.json starts from (smoke.py writes
    the same record for its own run, so that core/gate.py can read it)."""
    cli = hpaths.kicad_cli_info()
    info = {"run": hpaths.RUN, "designs": list(hpaths.DESIGNS), "fu2": hpaths.FU2,
            "target": str(hpaths.TARGET), "target_head": git_head(hpaths.TARGET),
            "kicad_cli": cli["path"], "kicad_cli_version": cli["version"],
            "kicad_demos": str(hpaths.KICAD_DEMOS),
            "gui_reading": "available" if gui_reading.AVAILABLE else "not available",
            "start_time": now(), "prepare": None, "batches": {}}
    if not gui_reading.AVAILABLE:
        info["gui_reading_reason"] = gui_reading.REASON
    return info


def popen(batch, stamp, log_dir):
    cwd, script, args, extra = BATCHES[batch]
    cmd = [hpaths.PYTHON, "-B", str(hpaths.HARNESS / cwd / script), *args]
    out = open(log_dir / f"{batch}.{stamp}.out", "wb")
    err = open(log_dir / f"{batch}.{stamp}.err", "wb")
    kw = {}
    if os.name == "nt":
        # No console window of its own and its own process group, so closing this terminal does not
        # end the batch. (DETACHED_PROCESS would make every kicad-cli a batch starts open a console.)
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(cmd, cwd=hpaths.HARNESS / cwd, env=hpaths.child_env(**extra),
                         stdin=subprocess.DEVNULL, stdout=out, stderr=err, **kw)
    out.close()
    err.close()
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--designs")
    ap.add_argument("--fu2", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--skip-prepare", action="store_true")
    ap.add_argument("--allow-drift", action="store_true")
    a = ap.parse_args(argv)
    hpaths.configure(run=a.run, designs=a.designs, fu2=True if a.fu2 else None)
    unknown = [d for d in hpaths.DESIGNS if d not in registry.NAMES]
    if unknown:
        ap.error(f"unknown design(s) {', '.join(unknown)}; valid names: {', '.join(registry.NAMES)}")
    names = [ALIASES.get(n.strip(), n.strip()) for n in a.only.split(",")] if a.only else list(BATCHES)
    bad = [n for n in names if n not in BATCHES]
    if bad:
        ap.error(f"unknown batch(es) {', '.join(bad)}; valid names: {', '.join(BATCHES)}")
    names = [n for n in BATCHES if n in names]

    run_dir = hpaths.RUN_DIR
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = hpaths.suite_out("meta")
    run_json = run_dir / "run.json"
    run = run_info()
    if run_json.is_file():
        old = json.loads(run_json.read_text(encoding="utf-8"))
        for key in ("designs", "fu2", "target", "target_head"):
            if old.get(key) != run[key]:
                print(f"run.py: run {hpaths.RUN!r} was started with {key} = {old.get(key)!r} and this "
                      f"call has {run[key]!r}; one run holds the records of one setup, so give this "
                      "call a new --run name", file=sys.stderr)
                return 2
        for name in names:
            pid = (old.get("batches", {}).get(name) or {}).get("pid")
            if pid and alive(pid):
                print(f"run.py: batch {name} of run {hpaths.RUN!r} is still running (pid {pid}); "
                      "wait for it or kill its whole process tree", file=sys.stderr)
                return 2
        run["start_time"] = old.get("start_time", run["start_time"])
        run["batches"] = old.get("batches", {})
        run["resumed"] = now()

    if a.skip_prepare:
        run["prepare"] = {"skipped": True}
    else:
        print(f"run.py: preparing run {hpaths.RUN!r} (prepare.py)", flush=True)
        r = subprocess.run([hpaths.PYTHON, "-B", str(hpaths.HARNESS / "prepare.py")], cwd=hpaths.HARNESS,
                           env=hpaths.child_env())
        report = run_dir / "prepare_report.json"
        run["prepare"] = {"skipped": False, "ok": r.returncode == 0, "allow_drift": a.allow_drift,
                          "report": report.name if report.is_file() else None}
    run_json.write_text(json.dumps(run, indent=1), encoding="utf-8")
    if run["prepare"].get("ok") is False and not a.allow_drift:
        print("run.py: prepare.py found fixtures that differ from the baselines' (or failed); not "
              f"launching. See {run_dir / 'prepare_report.json'}; --allow-drift launches anyway and "
              "records it in run.json", file=sys.stderr)
        return 1

    stamp = time.strftime("%m%d_%H%M%S")
    procs = {}
    for name in names:
        p = popen(name, stamp, meta)
        procs[name] = p
        run["batches"][name] = {"pid": p.pid, "started": now(), "out": f"meta/{name}.{stamp}.out",
                                "err": f"meta/{name}.{stamp}.err", "returncode": None}
        print(f"run.py: {name} pid {p.pid}", flush=True)
    run_json.write_text(json.dumps(run, indent=1), encoding="utf-8")
    if not a.wait:
        print(f"run.py: {len(procs)} batches detached; output in {meta}; re-run this command to resume "
              f"the run, core/gate.py {hpaths.RUN} <design> when they have finished", flush=True)
        return 0
    failed = []
    for name, p in procs.items():
        rc = p.wait()
        run["batches"][name]["returncode"] = rc
        print(f"run.py: {name} exited {rc}", flush=True)
        if rc != 0:
            failed.append(name)
    run_json.write_text(json.dumps(run, indent=1), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
