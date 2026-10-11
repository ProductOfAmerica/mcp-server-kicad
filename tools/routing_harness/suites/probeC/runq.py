"""runq: one job queue for this batch. Reads jobs.txt every 5 s (lines "subdir|outfile|arg arg ...",
'#' comments), runs each job line once, at most MAXP python processes at a time, with the Python that
runs this script and hpaths.child_env() (every child bootstraps itself, so no PYTHONPATH). A job runs
in suites/probeC/<subdir> and its output file lands in the run's probeC output directory
(<subdir>/<outfile>). Logs to runq.log there. Stops when every listed job has finished and jobs.txt
ends with a line "END".
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

HERE = Path(__file__).resolve().parent
OUT = hpaths.suite_out("probeC")
JOBS = HERE / (sys.argv[1] if len(sys.argv) > 1 else "jobs.txt")
PY = hpaths.PYTHON
ENV = hpaths.child_env()
MAXP = int(os.environ.get("RUNQ_MAXP", "3"))
log = open(OUT / "runq.log", "a", encoding="utf-8")


def say(msg):
    log.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    log.flush()


started, running = set(), []
for _ln in JOBS.read_text(encoding="utf-8").splitlines():  # finished earlier
    if _ln.count("|") >= 2 and not _ln.startswith("#"):
        _o = OUT / _ln.split("|")[0] / _ln.split("|")[1]
        if _o.exists() and "\nexit=" in _o.read_text(encoding="utf-8", errors="replace"):
            started.add(_ln.strip())
say(f"runq start pid {os.getpid()} maxp {MAXP}")
while True:
    lines = [ln.strip() for ln in JOBS.read_text(encoding="utf-8").splitlines()]
    jobs = [ln for ln in lines if ln and not ln.startswith("#") and ln != "END"]
    for job in list(running):
        p, line, fh, t0 = job
        if p.poll() is not None:
            fh.write(f"\nexit={p.returncode} secs={time.time() - t0:.0f}\n")
            fh.close()
            say(f"done rc={p.returncode} {time.time() - t0:.0f}s {line}")
            running.remove(job)
    for line in jobs:
        if len(running) >= MAXP:
            break
        if line in started:
            continue
        sub, out, args = line.split("|", 2)
        cwd = HERE / sub
        outp = OUT / sub / out
        outp.parent.mkdir(parents=True, exist_ok=True)
        fh = open(outp, "w", encoding="utf-8")
        p = subprocess.Popen([PY, "-B", *args.split()], cwd=str(cwd), stdout=fh,
                             stderr=subprocess.STDOUT, env=ENV)
        started.add(line)
        running.append((p, line, fh, time.time()))
        say(f"start pid {p.pid} {line}")
    if not running and "END" in lines and all(j in started for j in jobs):
        break
    time.sleep(5)
say("runq finished")
