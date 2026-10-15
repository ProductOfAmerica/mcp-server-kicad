"""The smoke subset of the routing harness: eleven steps against the checkout under test.

usage: smoke.py [--run NAME] [--steps LIST] [--scratch-root DIR] [--expect-collect N]

  --run NAME           the run directory the steps use: WORK/runs/NAME (default: smoke)
  --steps LIST         comma list of step numbers (default: all); run in this order: 1 to 8, 10, 9, 11
                       (step 9 reads the records of steps 7 and 10)
  --scratch-root DIR   the pressure test's scratch directory (pr57pt) while it still exists: steps 8
                       and 10 then also compare with the scratch crossban_probe.out and the scratch
                       follow-up 3 demo records; without it they check what they can and say so
  --expect-collect N   step 11: pytest's collected-test count at the commit before the harness

Everything lands in RUN_DIR (work/runs/smoke by default); each step's output is kept in
RUN_DIR/smoke/stepNN_*.log and the result in RUN_DIR/smoke_report.json. A step that fails stops the
run (step 10 is informational: its differences are reported, not blocking). Exit status 0 only if
every blocking step passed. The steps:

  1  paths and guard      hpaths.py: TARGET, KiCad demos found, GUI reading not available, guard ok
  2  imports              the registry loads X, Hb2, Hb, H, M2, E; proposal_impl and mine_impl import
  3  compat54             place_component of compat54 and of the package write the same bytes (UUIDs
                          aside); _lib_unit_style agrees with the package's on every Device symbol
  4  adapter shapes       analysis/slice1/smoke_x.py and smoke_x2.py
  5  judge self-check     core/selftest_named_merge.py
  6  prepare, identity    prepare.py: fixtures equal the baselines' (see its docstring)
  7  PI-15 and PI-17      pi_jobs.py on five jobs, designs X and Hb2: duplicate references and an
                          unresolved library symbol refuse, bytes unchanged
  8  crossing ban         analysis/crossban/crossban_probe.py: the hazard exists without the ban and
                          is gone with it, in the cli reading (Q1 to Q4 merge /A and /N; add_wires
                          along W adds nothing); the gui field reads n/a
  10 demo slice           run_sweeps.py wptn --limit=10 for design X: no error, no wrong write
  9  gate                 core/gate.py over the records of steps 7 and 10 (both must have run in
                          this run): gui reading not taken, wrong_gui null, wrong_cli 0
  11 repo hygiene         ruff, pyright, pytest collection, the sdist, forbidden strings, and git
                          status of tools/routing_harness (ignored files included, work/ aside)
                          unchanged since the smoke run started
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import argparse  # noqa: E402
import getpass  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tarfile  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402

UUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ORDER = (1, 2, 3, 4, 5, 6, 7, 8, 10, 9, 11)
INFORMATIONAL = {10}

def _account_name_rule():
    """The name of the account running this, as a whole word, or None. It is read at run time so
    that no file here has to spell a user name for the scan to look for it."""
    names = set()
    for get in (getpass.getuser, lambda: Path.home().name):
        try:
            names.add(get())
        except Exception:  # noqa: BLE001  (no name known: the path rules below still apply)
            pass
    names = sorted(n for n in names if n and len(n) >= 3)
    if not names:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9])",
                      re.IGNORECASE)


# What no committed file may carry. The path rules are spelled with character classes and escapes
# so that this file does not carry the strings itself; the user name is the running account's.
FORBIDDEN = {
    "Windows user path": re.compile(r"C:[\\/]+Use[r]s", re.IGNORECASE),
    "MSYS user path": re.compile(r"/c/Use[r]s", re.IGNORECASE),
    "user profile directory": re.compile(r"App[D]ata", re.IGNORECASE),
    "home directory path": re.compile(r"/home/[A-Za-z0-9._-]+/|/Use[r]s/[A-Za-z0-9._-]+/|\$HOME[\\/]"
                                      r"|%USERPROFIL[E]%|\$env:USERPROFIL[E]", re.IGNORECASE),
    "home configuration path": re.compile(r"~[\\/]+[.][A-Za-z]"),
    "virtual environment path": re.compile(r"[.]venv"),
    "em dash": re.compile(chr(0x2014)),
    "en dash": re.compile(chr(0x2013)),
}
if (_ACCOUNT := _account_name_rule()) is not None:
    FORBIDDEN["user name"] = _ACCOUNT
# The reports are verbatim historical documents: they may name the pinned export in prose, as the
# label %TEMP%\pr57pt\<export> (no drive, no user). Code, data and the README may not name a path to it.
FORBIDDEN_OUTSIDE_REPORTS = {
    "path to the 54160c1 export": re.compile(r"base54160c1[\\/]|[\\/]base54160c1"),
}

PI_EXPECT = {  # variant -> reason codes: PI-15 (true duplicate references) and PI-17 (orphan symbol)
    "m_edges/13_R1": ["dup_ref"], "m_edges/13_RQ": ["dup_ref"],
    "v10_dupref/C1": ["dup_ref"], "v10_dupref/CQ": ["dup_ref"],
    "m_orphan/R1_1": ["derived"],
}


def smoke_dir() -> Path:
    return hpaths.suite_out("smoke")


def run_script(label, script, args=(), env_extra=None, cwd=None):
    """Run a harness script as a child; (returncode, combined output). Output is kept in the log."""
    script = Path(script)
    p = subprocess.run([hpaths.PYTHON, "-B", str(script), *args], cwd=cwd or script.parent,
                       env=hpaths.child_env(**(env_extra or {})), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    (smoke_dir() / f"{label}.log").write_text(p.stdout, encoding="utf-8")
    return p.returncode, p.stdout


def records(path: Path):
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def git(*args):
    r = subprocess.run(["git", "-C", str(hpaths.REPO), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


# ------------------------------------------------------------------------------------- steps


def step1():
    rc, out = run_script("step01_hpaths", hpaths.HARNESS / "hpaths.py")
    want = Path(os.environ["PT_REPO"]).resolve() if os.environ.get("PT_REPO") else hpaths.REPO
    problems = []
    if rc != 0:
        problems.append(f"hpaths.py exited {rc}")
    if hpaths.TARGET != want:
        problems.append(f"TARGET {hpaths.TARGET} is not {want}")
    if not hpaths.KICAD_DEMOS.is_dir():
        problems.append(f"KICAD_DEMOS {hpaths.KICAD_DEMOS} does not exist")
    if "gui reading: not available" not in out:
        problems.append("hpaths.py does not report the GUI reading as not available")
    if "guard: mcp_server_kicad resolves under TARGET" not in out:
        problems.append("the guard did not pass")
    return not problems, "; ".join(problems) or f"TARGET {hpaths.TARGET}, demos {hpaths.KICAD_DEMOS}"


def step2():
    import registry

    names = ("X", "Hb2", "Hb", "H", "M2", "E")
    for n in names:
        registry.load(n)
    import mine_impl  # noqa: F401
    import proposal_impl  # noqa: F401

    return True, f"loaded {', '.join(names)}, proposal_impl and mine_impl"


def step3():
    import compat54
    from mcp_server_kicad import _cst, project, schematic

    d = smoke_dir() / "compat54"
    shutil.rmtree(d, ignore_errors=True)
    outs = {}
    for tag, fn in (("compat54", compat54.place_component), ("package", schematic.place_component)):
        p = d / tag / "t.kicad_sch"
        p.parent.mkdir(parents=True)
        project.create_schematic(schematic_path=str(p))
        fn(lib_id="Device:R", reference="R1", value="10k", x=101.6, y=101.6, schematic_path=str(p))
        outs[tag] = UUID_RE.sub(b"U", p.read_bytes())
    problems = []
    if outs["compat54"] != outs["package"]:
        problems.append("place_component writes different bytes in compat54 and in the package")
    lib = hpaths.KICAD_SYMBOLS / "Device.kicad_sym"
    root = _cst.parse(lib.read_bytes()).lists[0]
    subs = [sub for s in root.find_all("symbol") for sub in s.find_all("symbol")]
    note = ""
    try:
        from mcp_server_kicad import _connectivity

        ref = _connectivity._lib_unit_style
    except ImportError:
        ref = None
        note = "; no _connectivity at this target, _lib_unit_style comparison skipped"
    if ref is not None:
        bad = [sub.atoms[1].text for sub in subs if compat54._lib_unit_style(sub) != ref(sub)]
        if bad:
            problems.append(f"_lib_unit_style differs on {bad[:5]}")
    if not hasattr(schematic, "_lib_unit_style"):
        problems.append("compat54.install() left schematic._lib_unit_style missing")
    return (not problems, "; ".join(problems) or
            f"place_component bytes equal, _lib_unit_style equal on {len(subs)} Device sub-symbols{note}")


def step4():
    found = []
    for script, token in (("smoke_x.py", "smoke ok"), ("smoke_x2.py", "smoke2 ok")):
        rc, out = run_script(f"step04_{script[:-3]}", hpaths.ANALYSIS / "slice1" / script)
        if rc != 0 or token not in out:
            return False, f"{script}: exit {rc}, '{token}' {'found' if token in out else 'not found'}"
        found.append(token)
    return True, "printed " + " and ".join(found)


def step5():
    rc, out = run_script("step05_selftest", hpaths.CORE / "selftest_named_merge.py")
    return rc == 0 and "all pass" in out, f"exit {rc}, " + ("all pass" if "all pass" in out else "no 'all pass'")


def step6():
    rc, out = run_script("step06_prepare", hpaths.HARNESS / "prepare.py", cwd=hpaths.HARNESS)
    report = hpaths.RUN_DIR / "prepare_report.json"
    detail = ""
    if report.is_file():
        steps = json.loads(report.read_text(encoding="utf-8"))["steps"]
        detail = "; ".join(f"{k} {'ok' if v['ok'] else 'FAILED'}" + (
            f" ({v['equal']} of {v['of']})" if "equal" in v else "") for k, v in steps.items())
    return rc == 0, f"exit {rc}: {detail}"


def step7():
    script = hpaths.SUITES / "probeA" / "pin-identity" / "pi_jobs.py"
    rc, out = run_script("step07_pi_jobs", script,
                         ["--only", "m_edges/13_", "v10_dupref/", "m_orphan/", "--workers", "2"],
                         env_extra={"ROUTING_HARNESS_DESIGNS": "X,Hb2"})
    recs = records(hpaths.RUN_DIR / "probeA" / "records_pin-identity.jsonl")
    problems = [] if rc == 0 else [f"pi_jobs.py exited {rc}"]
    for r in recs:
        if r.get("status") in ("ERROR", "HARNESS_ERROR"):
            problems.append(f"{r.get('design')} {r.get('variant')}: {r.get('status')}")
    for design in ("X", "Hb2"):
        for variant, codes in PI_EXPECT.items():
            mine = [r for r in recs if r.get("design") == design and r.get("primary")
                    and r.get("variant") == variant]
            if not mine:
                problems.append(f"{design} {variant}: no record")
            for r in mine:
                if r.get("status") != "REFUSED" or r.get("codes") != codes or r.get("bytes_ok") is not True:
                    problems.append(f"{design} {variant}: status {r.get('status')}, codes "
                                    f"{r.get('codes')}, bytes_ok {r.get('bytes_ok')} (want REFUSED, {codes})")
    return not problems, "; ".join(problems[:6]) or "10 records (5 jobs x X, Hb2): REFUSED, codes as expected, bytes unchanged"


CROSSBAN_LINE = re.compile(
    r"^(?P<id>[STQ]\d+c?)\s.*?:\s(?P<status>OK|REFUSED|NOOP|ERROR)\b\s*"
    r"(?:cli=(?P<cli>.*?) dlv=(?P<dlv>\S+) gui=(?P<gui>.*?) dlv=(?P<gdlv>\S+)(?: FALSE-NOOP)?)?\s*$")


def crossban_parse(text):
    """{(section, id): {"status", "cli", "gui"}} from crossban_probe output."""
    out, section = {}, ""
    for line in text.splitlines():
        if line.startswith("==="):
            section = line.strip()
            continue
        m = CROSSBAN_LINE.match(line)
        if m:
            out[(section, m.group("id"))] = {"status": m.group("status"), "cli": m.group("cli"),
                                              "gui": m.group("gui")}
    return out


def step8(scratch_root=None):
    rc, out = run_script("step08_crossban", hpaths.ANALYSIS / "crossban" / "crossban_probe.py")
    got = crossban_parse(out)
    problems = [] if rc == 0 else [f"crossban_probe.py exited {rc}"]
    off = {i: v for (s, i), v in got.items() if s.endswith("CROSS_BAN=False")}
    on = {i: v for (s, i), v in got.items() if s.endswith("CROSS_BAN=True")}
    if len(got) < 30:
        problems.append(f"{len(got)} result lines parsed, expected 30 (S1 to S7, T1 to T4, Q0 to Q9 ban off and on)")
    # The hazard the ban exists for: a label (Q1 to Q3) or a junction (Q4) at a crossing joins
    # /A and /N. Since the package splits wires at junctions (step 8) nothing is cut any more,
    # and add_wires along W (Q5) adds nothing, W covering it, so its reading is clean.
    for i in ("Q1", "Q2", "Q3", "Q4"):
        v = off.get(i)
        if not v or v["status"] != "OK" or not (v["cli"] or "").startswith("WRONG"):
            problems.append(f"ban off {i}: cli reading not WRONG ({v and v['cli']})")
            continue
        if "bad_merges" not in v["cli"] or "'/A'" not in v["cli"] or "'/N'" not in v["cli"]:
            problems.append(f"ban off {i}: /A and /N not merged ({v['cli']})")
        if "splits=" in v["cli"]:
            problems.append(f"ban off {i}: a net split ({v['cli']})")
    for i, status in (("Q5", "OK"), ("Q4c", "REFUSED"), ("Q5c", "OK")):
        v = off.get(i)
        if not v or v["status"] != status or (status == "OK" and v["cli"] != "clean"):
            problems.append(f"ban off {i}: {v and (v['status'], v['cli'])}, expected {status} clean")
    for i in ("Q1", "Q2", "Q3", "Q4", "Q5"):
        ctrl, v = off.get(i + "c"), on.get(i)
        if not ctrl or not v or (ctrl["status"], ctrl["cli"]) != (v["status"], v["cli"]):
            problems.append(f"ban on {i}: cli {v and v['cli']} differs from its control {ctrl and ctrl['cli']}")
    for tag, group in (("off", off), ("on", on)):
        if group.get("Q9", {}).get("status") != "REFUSED":
            problems.append(f"ban {tag} Q9: not REFUSED")
    notna = [f"{i} ({s.replace('=== ', '').split(':')[0]})" for (s, i), v in got.items()
             if v["status"] in ("OK", "NOOP") and v["gui"] is not None and not v["gui"].startswith("n/a")]
    if notna:
        problems.append(f"gui field is not n/a on {notna[:4]}")
    note = f"{len(got)} result lines parsed"
    if scratch_root:
        ref_file = Path(scratch_root) / "coarse" / "h_build" / "crossban_probe.out"
        if ref_file.is_file():
            ref = crossban_parse(ref_file.read_text(encoding="utf-8", errors="replace"))
            # The scratch run took the repo's add_junctions and add_wires at 54160c1, which
            # wrote junctions on unsplit wires; since step 8 these lines differ by design.
            changed = {"Q4", "Q4c", "Q5", "Q5c"}
            diff = [f"{i}: status {got[k]['status']} cli {got[k]['cli']} vs scratch status "
                    f"{ref[k]['status']} cli {ref[k]['cli']}"
                    for k in ref for i in [k[1]] if i not in changed
                    and (k not in got or got[k]["status"] != ref[k]["status"]
                         or got[k]["cli"] != ref[k]["cli"])]
            problems += [f"differs from the scratch output: {d}" for d in diff[:5]]
            note += f"; compared with {len(ref)} scratch lines ({', '.join(sorted(changed))} expected to differ)"
        else:
            note += "; scratch crossban_probe.out not found, not compared"
    else:
        note += "; no --scratch-root, not compared with the scratch output"
    return not problems, "; ".join(problems[:6]) or note


def step10(scratch_root=None):
    rc, out = run_script("step10_demo", hpaths.SUITES / "demo" / "run_sweeps.py",
                         ["wptn", "--limit=10", "--workers=4"], env_extra={"ROUTING_HARNESS_DESIGNS": "X"})
    recs = [r for r in records(hpaths.RUN_DIR / "demo" / "records.jsonl")
            if r.get("sample") == "demo_wptn" and r.get("design") == "X"]
    problems = [] if rc == 0 else [f"run_sweeps.py exited {rc}"]
    if len(recs) != 10:
        problems.append(f"{len(recs)} records, expected 10")
    errs = [f"{r['group']} {r['case']}: {r['status']}" for r in recs if r.get("status") in ("ERROR", "HARNESS_ERROR")]
    wrong = [f"{r['group']} {r['case']}" for r in recs if r.get("cli_wrong")]
    if errs:
        problems.append(f"errors: {errs[:3]}")
    if wrong:
        problems.append(f"wrong_cli: {wrong[:3]}")
    note = f"{len(recs)} records, statuses {sorted({r.get('status') for r in recs})}"
    if scratch_root:
        ref_file = Path(scratch_root) / "coarse" / "rerun7" / "demo" / "records.jsonl"
        if ref_file.is_file():
            ref = {(r["group"], r["case"]): (r["status"], r.get("codes"))
                   for r in records(ref_file) if r.get("sample") == "demo_wptn" and r.get("design") == "X"}
            diffs = [f"{r['group']} {r['case']}: {r['status']} {r.get('codes')} here, "
                     f"{ref[(r['group'], r['case'])][0]} {ref[(r['group'], r['case'])][1]} in scratch"
                     for r in recs if (r["group"], r["case"]) in ref
                     and ref[(r["group"], r["case"])] != (r["status"], r.get("codes"))]
            absent = [f"{r['group']} {r['case']}" for r in recs if (r["group"], r["case"]) not in ref]
            note += f"; {len(recs) - len(absent)} compared with scratch X records: {len(diffs)} differ"
            if diffs or absent:
                note += " (explain each: " + "; ".join(diffs + [f"{a} not in scratch" for a in absent]) + ")"
        else:
            note += "; scratch records not found, not compared"
    else:
        note += "; no --scratch-root, not compared with the scratch records"
    return not problems, "; ".join(problems) or note


def step9():
    out_json = hpaths.RUN_DIR / "gate_smoke.json"
    rc, out = run_script("step09_gate", hpaths.CORE / "gate.py", [hpaths.RUN, "X", "--json", str(out_json)])
    problems = [] if rc == 0 else [f"gate.py exited {rc}"]
    first = out.splitlines()[0] if out.strip() else ""
    if not first.startswith("gui reading: not taken"):
        problems.append(f"first line is {first!r}, not the gui-not-taken notice")
    if out_json.is_file():
        g = json.loads(out_json.read_text(encoding="utf-8"))
        for name in ("probeA", "probeB", "probeC", "llm", "layouts", "demo"):
            gate = (g.get(name) or {}).get("gate")
            if gate is None:
                if name in ("probeA", "demo"):  # steps 7 and 10 write these
                    problems.append(f"{name}: no gate ({(g.get(name) or {}).get('missing', 'absent')})")
                continue
            if "wrong_gui" not in gate or gate["wrong_gui"] is not None:
                problems.append(f"{name}: wrong_gui is {gate.get('wrong_gui', '<absent>')!r}, not null")
            if gate.get("wrong_cli", 0) != 0:
                problems.append(f"{name}: wrong_cli {gate['wrong_cli']}")
        calls = {n: (g.get(n) or {}).get("gate", {}).get("calls", 0) for n in ("probeA", "demo")}
    else:
        problems.append(f"{out_json.name} was not written")
        calls = {}
    return not problems, "; ".join(problems[:6]) or f"gui reading not taken, wrong_gui null, wrong_cli 0 (calls {calls})"


def hygiene_files():
    """The files of tools/routing_harness that would be committed: tracked, plus untracked and not ignored."""
    rc, out = git("ls-files", "-co", "--exclude-standard", "-z", "--", "tools/routing_harness")
    if rc == 0 and out:
        return [hpaths.REPO / p for p in out.split("\0") if p]
    return [p for p in hpaths.HARNESS.rglob("*") if p.is_file() and hpaths.WORK not in p.parents]


def harness_status():
    """git status of tools/routing_harness with ignored files (so a __pycache__ shows), work/ aside."""
    return git("status", "--porcelain", "--untracked-files=all", "--ignored", "--",
               "tools/routing_harness", ":(exclude)tools/routing_harness/work")[1]


def step11(expect_collect=None, status_before=None):
    repo = hpaths.REPO
    problems, notes = [], []
    py = hpaths.PYTHON

    def run(label, cmd):
        p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
        (smoke_dir() / f"step11_{label}.log").write_text(p.stdout + p.stderr, encoding="utf-8")
        return p

    if status_before is None:
        status_before = harness_status()
    for label, cmd in (("ruff_check", [py, "-m", "ruff", "check", "."]),
                       ("ruff_format", [py, "-m", "ruff", "format", "--check", "."]),
                       ("pyright", [py, "-m", "pyright"])):
        p = run(label, cmd)
        if p.returncode != 0:
            problems.append(f"{label} exited {p.returncode}")
    p = run("collect_default", [py, "-m", "pytest", "--collect-only", "-q"])
    m = re.search(r"(\d+) tests? collected", p.stdout)
    default = int(m.group(1)) if m else None
    p2 = run("collect_tests", [py, "-m", "pytest", "--collect-only", "-q", "tests"])
    m2 = re.search(r"(\d+) tests? collected", p2.stdout)
    in_tests = int(m2.group(1)) if m2 else None
    if default is None or default != in_tests:
        problems.append(f"pytest collects {default} tests by default but {in_tests} under tests/")
    if expect_collect is not None and default != expect_collect:
        problems.append(f"pytest collects {default} tests, expected {expect_collect}")
    notes.append(f"pytest collects {default} tests")
    uv = shutil.which("uv")
    if uv:
        dist = smoke_dir() / "dist"
        shutil.rmtree(dist, ignore_errors=True)
        p = run("uv_build", [uv, "build", "--sdist", "--out-dir", str(dist)])
        sdists = sorted(dist.glob("*.tar.gz"))
        if p.returncode != 0 or not sdists:
            problems.append(f"uv build --sdist exited {p.returncode}")
        else:
            with tarfile.open(sdists[-1]) as tf:
                names = tf.getnames()
            leaked = [n for n in names if "tools/routing_harness" in n]
            if leaked:
                problems.append(f"the sdist holds {len(leaked)} harness paths, e.g. {leaked[0]}")
            notes.append(f"sdist {sdists[-1].name}: {len(names)} entries, no harness path")
    else:
        problems.append("uv is not on PATH, the sdist was not built")
    hits = []
    for f in hygiene_files():
        try:
            text = f.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            hits.append(f"{f.name}: not UTF-8")
            continue
        rules = dict(FORBIDDEN)
        if "reports" not in f.relative_to(hpaths.HARNESS).parts[:1]:
            rules.update(FORBIDDEN_OUTSIDE_REPORTS)
        for label, rx in rules.items():
            if rx.search(text):
                hits.append(f"{f.relative_to(hpaths.REPO).as_posix()}: {label}")
    if hits:
        problems.append(f"{len(hits)} forbidden strings, e.g. {hits[:4]}")
    notes.append(f"forbidden-string scan over {len(hygiene_files())} files")
    status_after = harness_status()
    if status_after != status_before:
        problems.append("git status of tools/routing_harness changed during the run: "
                        + "; ".join(sorted(set(status_after.splitlines()) ^ set(status_before.splitlines()))[:4]))
    else:
        notes.append("git status of tools/routing_harness unchanged by the run"
                     + (" (clean)" if not status_after.strip() else ""))
    return not problems, "; ".join(problems[:6]) or "; ".join(notes)


# ---------------------------------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", default="smoke")
    ap.add_argument("--steps")
    ap.add_argument("--scratch-root")
    ap.add_argument("--expect-collect", type=int)
    a = ap.parse_args(argv)
    hpaths.configure(run=a.run, designs="X")
    chosen = [int(s) for s in a.steps.split(",")] if a.steps else list(ORDER)
    bad = [s for s in chosen if s not in ORDER]
    if bad:
        ap.error(f"unknown step(s) {bad}; steps are {sorted(ORDER)}")
    order = [s for s in ORDER if s in chosen]

    # judge3 and harness make temp directories when imported; keep every temp file under the run.
    tmp = smoke_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp)
    import run as run_py  # run.py: run_info()

    info = run_py.run_info()
    info["smoke"] = True
    (hpaths.RUN_DIR / "run.json").write_text(json.dumps(info, indent=1), encoding="utf-8")

    status_at_start = harness_status()
    fns = {1: step1, 2: step2, 3: step3, 4: step4, 5: step5, 6: step6, 7: step7,
           8: lambda: step8(a.scratch_root), 9: step9, 10: lambda: step10(a.scratch_root),
           11: lambda: step11(a.expect_collect, status_at_start)}
    names = {1: "paths and guard", 2: "imports", 3: "compat54", 4: "adapter shapes", 5: "judge self-check",
             6: "prepare and identity", 7: "PI-15 and PI-17", 8: "crossing ban", 9: "gate",
             10: "demo slice (informational)", 11: "repo hygiene"}
    results, stopped = [], False
    for n in order:
        t0 = time.time()
        print(f"step {n:>2} {names[n]}: running", flush=True)
        try:
            ok, detail = fns[n]()
        except Exception as e:  # noqa: BLE001  (a step that cannot run is a failed step)
            ok, detail = False, f"{type(e).__name__}: {e}"
            (smoke_dir() / f"step{n:02d}_exception.log").write_text(traceback.format_exc(), encoding="utf-8")
        results.append({"step": n, "name": names[n], "ok": bool(ok), "seconds": round(time.time() - t0, 1),
                        "detail": detail, "blocking": n not in INFORMATIONAL})
        print(f"step {n:>2} {names[n]}: {'PASS' if ok else 'FAIL'} ({results[-1]['seconds']}s) {detail}", flush=True)
        if not ok and n not in INFORMATIONAL:
            stopped = True
            print(f"smoke: stopping at step {n}", flush=True)
            break
    passed = not stopped
    (hpaths.RUN_DIR / "smoke_report.json").write_text(
        json.dumps({"run": hpaths.RUN, "passed": passed, "steps": results}, indent=1), encoding="utf-8")
    print(f"smoke: {'passed' if passed else 'FAILED'} ({len(results)} of {len(order)} steps run); "
          f"report in {hpaths.RUN_DIR / 'smoke_report.json'}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
