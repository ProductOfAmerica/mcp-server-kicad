"""Poll every records/parts .jsonl of a run (not the quarantine) and print one line per NEW record that
is a wrong write (cli_wrong or gui_wrong truthy) or a false no-op, or a harness error, made by one of
the run's selected designs (hpaths.DESIGNS; other designs, and the repo add_label fallbacks, only report
errors, and the summaries count the rest). Prints a heartbeat with record counts every 30 minutes.
State (line counts already reported, so a re-armed monitor does not repeat itself) is kept in
RUN_DIR/meta/watch_state.json.

usage: watch_bad.py [run name or run directory] [--once]
       (default: the current run, ROUTING_HARNESS_RUN; --once makes a single pass and exits)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

_args = [a for a in sys.argv[1:] if not a.startswith("--")]
ONCE = "--once" in sys.argv[1:]
R2 = hpaths.run_dir(_args[0]) if _args else hpaths.RUN_DIR
SELECTED = tuple(hpaths.DESIGNS)
STATE = R2 / "meta" / "watch_state.json"  # line counts already reported (survives a re-arm)
seen: dict = ({Path(k): v for k, v in json.loads(STATE.read_text()).items()}
              if STATE.exists() else {})
last_beat = 0.0
SKIP = {"tmp", "work", "wrong", "__pycache__", "fixtures", "bases", "base", "libs"}


def _jsonl_files():
    """Every .jsonl under the run outside scratch and quarantine dirs; races with deleted dirs are
    ignored (os.walk's onerror)."""
    for dirpath, dirnames, filenames in os.walk(R2, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith("_superseded")]
        for fn in filenames:
            if fn.endswith(".jsonl"):
                yield Path(dirpath) / fn
while True:
    total = 0
    for f in sorted(_jsonl_files()):
        rel = f.relative_to(R2)
        if "_superseded" in rel.parts[0] or "tmp" in rel.parts or "work" in rel.parts:
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        n0 = seen.get(f, 0)
        if len(lines) < n0:
            n0 = 0  # truncated (a job restarted)
        for ln in lines[n0:]:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            bad = []
            if r.get("cli_wrong") or r.get("gui_wrong"):
                bad.append(f"WRONG cli={r.get('cli_wrong')} gui={r.get('gui_wrong')}")
            if r.get("false_noop"):
                bad.append("FALSE-NOOP")
            if r.get("status") in ("HARNESS_ERROR", "ERROR"):
                bad.append(r.get("status"))
            if r.get("tool") == "add_label" or r.get("design") not in SELECTED:
                # repo add_label fallbacks and other designs: errors only; summaries count the rest
                bad = [b for b in bad if b in ("HARNESS_ERROR", "ERROR")]
            pads = r.get("pads") or []
            if bad == ["FALSE-NOOP"] and pads and all(str(x[0]).startswith("#") for x in pads):
                bad = []  # DEC-23 judge limit: kicad-cli lists no node for a '#' reference
            if bad:
                who = r.get("design")
                what = r.get("kind") or r.get("sample") or ""
                case = r.get("case") or r.get("variant") or r.get("group")
                print(f"{rel} | {who} | {what} | {case} | {' '.join(bad)} | "
                      f"{str(r.get('msg_head') or r.get('error') or '')[:100]!r}", flush=True)
        seen[f] = len(lines)
        total += len(lines)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({str(k): v for k, v in seen.items()}))
    if time.time() - last_beat > 1800:
        print(f"heartbeat {time.strftime('%H:%M')}: {total} records in {len(seen)} files",
              flush=True)
        last_beat = time.time()
    if ONCE:
        break
    time.sleep(60)
