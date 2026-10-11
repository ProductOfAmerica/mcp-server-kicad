"""Follow-up 2: run the probeB script copies (designs M2, H, Hb, E), at most N processes at a time (default 6), each with its own log
in logs/<job>.out and its own records file rec/<job>.jsonl (env PROBEB_JOB), all under the run's
probeB output directory (hpaths.suite_out). Resumable: a job that
finished with rc 0 leaves logs/<job>.done and is skipped next time; a job without it has any
partial records file moved to rec_partial/ before it reruns.
Usage: uv run python tools/routing_harness/suites/probeB/driver.py [N] [job-substring ...]"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

SRC = Path(__file__).resolve().parent  # read-only: scripts/ (and the child's cwd)
OUT = hpaths.suite_out("probeB")  # logs, rec and rec_partial are written here
S = SRC / "scripts"

JOBS = []
NSH = 16  # follow-up 1 used 12; the s09 Part A shards are the critical path, so they start first
for i in range(NSH):
    JOBS.append((f"s09_A{i}", [str(S / "buses" / "s09_demo_sweep.py"), "A", str(i), str(NSH)]))
JOBS.append(("s09_B", [str(S / "buses" / "s09_demo_sweep.py"), "B"]))
for cat in ("hierarchy", "buses", "units-pads", "naming"):
    for p in sorted((S / cat).glob("*.py")):
        if p.stem == "s09_demo_sweep":
            continue
        JOBS.append((p.stem, [str(p)]))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    sel = sys.argv[2:]
    logs, rec, part = OUT / "logs", OUT / "rec", OUT / "rec_partial"
    logs.mkdir(exist_ok=True)
    rec.mkdir(exist_ok=True)
    part.mkdir(exist_ok=True)
    jobs = [j for j in JOBS if (not sel or any(s in j[0] for s in sel))
            and not (logs / f"{j[0]}.done").exists()]
    running = []
    t0 = time.time()
    while jobs or running:
        while jobs and len(running) < n:
            name, args = jobs.pop(0)
            rf = rec / f"{name}.jsonl"
            if rf.exists():
                shutil.move(str(rf), str(part / f"{name}.{int(time.time())}.jsonl"))
            env = hpaths.child_env(PROBEB_JOB=name)
            log = open(logs / f"{name}.out", "w", encoding="utf-8")
            p = subprocess.Popen([hpaths.PYTHON, "-B", *args], stdout=log,
                                 stderr=subprocess.STDOUT, env=env, cwd=str(SRC))
            running.append((name, p, log, time.time()))
            print(f"[{time.time() - t0:7.1f}s] start {name}", flush=True)
        for item in list(running):
            name, p, log, ts = item
            if p.poll() is not None:
                log.close()
                running.remove(item)
                if p.returncode == 0:
                    (logs / f"{name}.done").write_text("ok\n")
                print(f"[{time.time() - t0:7.1f}s] done  {name} rc={p.returncode}"
                      f" ({time.time() - ts:.0f}s)", flush=True)
        time.sleep(1)
    print(f"all done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
