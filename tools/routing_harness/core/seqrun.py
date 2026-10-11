"""seqrun: run a list of jobs from a JSON file, at most MAXP at a time, resumably.

Each job: {"cwd": dir relative to tools/routing_harness, "args": [script, arg...],
"out": file relative to the run directory, "env": {...} (optional)}.
A job whose out file already ends with a line "exit=0" is skipped (it finished in an earlier run);
any other job is (re)run from scratch, its out file overwritten, and "exit=<rc>" appended when the
process ends. Python runs as this interpreter (hpaths.PYTHON) with hpaths.child_env() plus the job's
env; the repository under test is whatever hpaths selected (PT_REPO, else this checkout). Log lines
go to RUN_DIR/meta/<jobfile stem>.log.

usage: seqrun.py JOBFILE.json [MAXP]
       (JOBFILE is a path from the current directory, else from tools/routing_harness)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402


def finished(out: Path) -> bool:
    if not out.exists():
        return False
    tail = out.read_bytes()[-200:].decode("utf-8", errors="replace").strip().splitlines()
    return bool(tail) and tail[-1].strip() == "exit=0"


def main():
    jobfile = Path(sys.argv[1])
    if not jobfile.is_file() and not jobfile.is_absolute() and (hpaths.HARNESS / jobfile).is_file():
        jobfile = hpaths.HARNESS / jobfile
    jobfile = jobfile.resolve()
    maxp = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    jobs = json.loads(jobfile.read_text(encoding="utf-8"))
    meta = hpaths.suite_out("meta")
    log = open(meta / f"{jobfile.stem}.log", "a", encoding="utf-8")

    def say(msg):
        log.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")
        log.flush()

    def out_of(j):
        return hpaths.RUN_DIR / j["out"]

    say(f"seqrun start pid {os.getpid()}, {len(jobs)} jobs, maxp {maxp}")
    pending = []
    for j in jobs:
        if finished(out_of(j)):
            say(f"skip (finished earlier) {j['out']}")
        else:
            pending.append(j)
    running = []
    while pending or running:
        for item in list(running):
            p, j, fh, t0 = item
            if p.poll() is not None:
                fh.write(f"\nexit={p.returncode}\n")
                fh.close()
                say(f"done rc={p.returncode} {time.time() - t0:.0f}s {j['out']}")
                running.remove(item)
        while pending and len(running) < maxp:
            j = pending.pop(0)
            env = hpaths.child_env(**j.get("env", {}))
            out_of(j).parent.mkdir(parents=True, exist_ok=True)
            fh = open(out_of(j), "w", encoding="utf-8")
            p = subprocess.Popen([hpaths.PYTHON, "-B", *j["args"]], cwd=hpaths.HARNESS / j["cwd"],
                                 stdout=fh, stderr=subprocess.STDOUT, env=env)
            running.append((p, j, fh, time.time()))
            say(f"start pid {p.pid} {j['out']}: {' '.join(j['args'])}")
        time.sleep(3)
    say("seqrun finished")


if __name__ == "__main__":
    main()
