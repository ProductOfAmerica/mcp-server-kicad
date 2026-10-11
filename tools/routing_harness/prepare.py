"""Build one run's prepare-time fixtures and check them against the fixtures the baselines were taken on.

usage: prepare.py [--run NAME]        (default run: ROUTING_HARNESS_RUN, else "default")

Every suite starts from inputs that are copies and derivatives of KiCad's own demo projects, taken
from the installed KiCad (hpaths.KICAD_DEMOS) into RUN_DIR/fixtures. The measurements are only
comparable with the baselines if those inputs are what the baselines used, so this script builds them
and compares each with baselines/fixtures_expected.json (and the demo and layout-base lists with
baselines/demo_followup1.json and baselines/layouts_followup1_bases.json). Five steps:

  1 layouts stock fixtures  the stock bases of layouts_stock (three demo sheets, standalone copies)
  2 probeB vhp              probeB's vhp.kicad_sym (a demo-embedded symbol with hidden power pins)
  3 probeC perf             suites/probeC/perf/build_fixtures.py: the perf fixtures and their checks
  4 demo                    suites/demo/run_sweeps.py --prepare-only: repathed sheets and candidate pins
  5 layout bases            suites/layouts_stock/runner.py --only none, then check_bases.py: 67 bases

Steps 1 and 2 are done here (pure CST, no kicad-cli); 3 to 5 run the suites' own scripts as child
processes (their output goes to RUN_DIR/meta/prepare_<step>.log). The result is written to
RUN_DIR/prepare_report.json and the exit status is 0 only if every step passed. The report also states
the kicad-cli version: the baselines come from KiCad 9.0.8's demos and kicad-cli, so a different
version is reported as drift (a difference in a fixture there is a finding, not something to fix by
editing an expectation).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

from mcp_server_kicad import _cst  # noqa: E402

EXPECTED_KICAD = "9.0.8"  # the version of the demos and kicad-cli the baselines were taken with
STOCK = {  # layouts_stock's stock bases: name -> demo sheet
    "ampli_ht": "complex_hierarchy/ampli_ht.kicad_sch",
    "fpga-hp-banks": "vme-wren/fpga-hp-banks.kicad_sch",
    "tinytapeout-demo": "tiny_tapeout/tinytapeout-demo.kicad_sch",
}
UUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
DEAD_PATH = "/ffffffff-0000-0000-0000-000000000000"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(data: bytes) -> str:
    """check_bases' rule: sha256 of the bytes with every UUID replaced by U, first 20 hex digits."""
    return hashlib.sha256(UUID_RE.sub(b"U", data)).hexdigest()[:20]


def baseline(name: str):
    return json.loads((hpaths.BASELINES / name).read_text(encoding="utf-8"))


def standalone_copy(src, dst):
    """vcommon.standalone_copy (as pfix.py copies it): every symbol's first instance path becomes
    /<this file's uuid> and each further one a path that exists nowhere, so kicad-cli lists the
    sheet's unnamed nets when it exports the copy on its own."""
    tree = _cst.parse(Path(src).read_bytes())
    root = tree.lists[0]
    u = root.find("uuid").atoms[1].text
    for s in root.find_all("symbol"):
        inst = s.find("instances")
        if inst is None:
            continue
        first = True
        for proj in inst.find_all("project"):
            for path in proj.find_all("path"):
                path.atoms[1].set_text(f"/{u}" if first else DEAD_PATH)
                first = False
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(_cst.serialize(tree))
    return str(dst)


def extract_vhp(src, dst):
    """verify-sem-naming/v01_extract_628128.py: the lib_symbols entry interf_u:628128 of the demo
    interf_u/interf_u.kicad_sch (an SRAM with hidden power_in pins VCC 32 and GND 16), renamed
    628128 and wrapped in a symbol library, written with LF."""
    root = _cst.parse(Path(src).read_bytes()).lists[0]
    ls = root.find("lib_symbols")
    sym = next(s for s in ls.lists if s.atoms[0].text == "symbol" and s.atoms[1].text == "interf_u:628128")
    sym = sym.copy()
    sym.atoms[1].set_text("628128")
    text = _cst.serialize(sym).decode()
    lib = ('(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") '
           '(generator_version "9.0")\n  ' + text.strip() + "\n)\n")
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(lib.encode("utf-8"))
    return str(dst)


def say(msg):
    print(msg, flush=True)


def child(name, script: Path, args=(), capture_stdout=False):
    """Run one suite script as a child (cwd: its own directory); output to meta/prepare_<name>.log."""
    log = hpaths.suite_out("meta") / f"prepare_{name}.log"
    t0 = time.time()
    p = subprocess.run([hpaths.PYTHON, "-B", str(script), *args], cwd=script.parent, env=hpaths.child_env(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    log.write_text(p.stdout + ("\n--- stderr ---\n" + p.stderr if p.stderr else ""), encoding="utf-8")
    res = {"returncode": p.returncode, "seconds": round(time.time() - t0, 1),
           "log": log.relative_to(hpaths.RUN_DIR).as_posix()}
    if p.returncode != 0:
        tail = (p.stdout + p.stderr).strip().splitlines()[-12:]
        res["tail"] = tail
    if capture_stdout:
        res["stdout"] = p.stdout
    return res


# ---------------------------------------------------------------------------------------- steps


def step_layouts_stock():
    exp = baseline("fixtures_expected.json")["layouts_stock"]
    differ = []
    for name, rel in STOCK.items():
        dst = hpaths.FIXTURES / "layouts_stock" / f"{name}.base.kicad_sch"
        standalone_copy(hpaths.KICAD_DEMOS / rel, dst)
        if sha256(dst.read_bytes()) != exp[name]:
            differ.append(name)
    return {"ok": not differ, "equal": len(STOCK) - len(differ), "of": len(STOCK), "differ": differ}


def step_probeB_vhp():
    exp = baseline("fixtures_expected.json")["probeB_vhp"]
    dst = hpaths.FIXTURES / "probeB" / "vhp.kicad_sym"
    extract_vhp(hpaths.KICAD_DEMOS / "interf_u" / "interf_u.kicad_sch", dst)
    equal = sha256(dst.read_bytes()) == exp
    return {"ok": equal, "equal": int(equal), "of": 1, "differ": [] if equal else ["vhp.kicad_sym"]}


def step_probeC_perf():
    script = hpaths.SUITES / "probeC" / "perf" / "build_fixtures.py"
    res = child("probeC_perf", script)
    exp = baseline("fixtures_expected.json")["probeC_perf"]
    base = hpaths.FIXTURES / "probeC" / "perf"
    differ, missing = [], []
    for key, want in exp.items():
        f = base / key
        if not f.is_file():
            missing.append(key)
            continue
        data = f.read_bytes()
        got = sha256(data) if "sha256" in want else norm(data)
        if got != next(iter(want.values())):
            differ.append(key)
    flags = []
    report = base / "build_report.json"
    if report.is_file():
        def walk(x, path):
            if isinstance(x, dict):
                for k, v in x.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(x, bool):
                if not x:
                    flags.append(path)
            elif isinstance(x, list) and path.endswith(".missing") and x:
                flags.append(f"{path}={x}")
        walk(json.loads(report.read_text(encoding="utf-8")), "report")
    else:
        flags.append("build_report.json not written")
    res.update({"equal": len(exp) - len(differ) - len(missing), "of": len(exp), "differ": differ,
                "missing": missing, "build_report_problems": flags})
    res["ok"] = res["returncode"] == 0 and not differ and not missing and not flags
    return res


def step_demo():
    script = hpaths.SUITES / "demo" / "run_sweeps.py"
    res = child("demo", script, ["--prepare-only"])
    fu1 = baseline("demo_followup1.json")
    prep_file = hpaths.RUN_DIR / "demo" / "prepare.json"
    if not prep_file.is_file():
        res.update({"ok": False, "error": "demo/prepare.json was not written"})
        return res
    prep = json.loads(prep_file.read_text(encoding="utf-8"))
    bytes_differ = sorted(rel for rel, v in fu1.items()
                          if rel not in prep or prep[rel].get("sha256") != v["sha256"])
    cand_differ = sorted(rel for rel, v in fu1.items() if rel in prep
                         and [list(x) for x in prep[rel].get("cand", [])] != [list(x) for x in v["cand"]])
    res.update({"sheets": len(fu1), "same_bytes_as_followup1": len(fu1) - len(bytes_differ),
                "bytes_differ": bytes_differ, "candidate_lists_differ": cand_differ})
    res["ok"] = res["returncode"] == 0 and not bytes_differ and not cand_differ
    return res


def step_layout_bases():
    runner = hpaths.SUITES / "layouts_stock" / "runner.py"
    check = hpaths.SUITES / "layouts_stock" / "check_bases.py"
    res = child("layouts_runner", runner, ["--only", "none"])
    res2 = child("layouts_check_bases", check, capture_stdout=True)
    out = {"runner": {k: v for k, v in res.items()},
           "check_bases": {k: v for k, v in res2.items() if k != "stdout"}}
    try:
        counts = json.loads(res2["stdout"])
        equal, differ, missing = counts["equal"], counts["differ"], counts["missing"]
    except (ValueError, KeyError):
        out.update({"ok": False, "error": "check_bases printed no result", "equal": 0, "of": 67})
        return out
    out.update({"equal": equal, "of": len(baseline("layouts_followup1_bases.json")),
                "differ": differ, "missing": missing})
    out["ok"] = (res["returncode"] == 0 and res2["returncode"] == 0 and not differ and not missing
                 and equal == out["of"])
    return out


STEPS = (("layouts_stock_fixtures", "layouts stock fixtures", step_layouts_stock),
         ("probeB_vhp", "probeB vhp library", step_probeB_vhp),
         ("probeC_perf", "probeC perf fixtures", step_probeC_perf),
         ("demo", "demo repaths and candidates", step_demo),
         ("layout_bases", "layout bases", step_layout_bases))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", help="run name (default: ROUTING_HARNESS_RUN, else default)")
    a = ap.parse_args(argv)
    hpaths.configure(run=a.run)
    hpaths.RUN_DIR.mkdir(parents=True, exist_ok=True)
    cli = hpaths.kicad_cli_info()
    drift = cli["version"] != EXPECTED_KICAD
    say(f"run {hpaths.RUN}: kicad-cli {cli['version'] or 'unknown'} "
        f"({'differs from' if drift else 'same as'} the baselines' {EXPECTED_KICAD})")
    steps = {}
    for i, (key, label, fn) in enumerate(STEPS, 1):
        say(f"[{i}/{len(STEPS)}] {label}: running")
        try:
            steps[key] = fn()
        except Exception as e:  # noqa: BLE001  (a missing demo file or a crash fails the step, not the script)
            steps[key] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        r = steps[key]
        detail = (f"{r['equal']} of {r['of']} equal" if "equal" in r and "of" in r else r.get("error", ""))
        say(f"[{i}/{len(STEPS)}] {label}: {'ok' if r['ok'] else 'FAILED'} {detail}".rstrip())
        for line in r.get("tail", []):
            say(f"      {line}")
    report = {"run": hpaths.RUN, "ok": all(s["ok"] for s in steps.values()),
              "kicad_cli": cli["path"], "kicad_cli_version": cli["version"],
              "baseline_kicad_version": EXPECTED_KICAD, "kicad_version_drift": drift,
              "kicad_demos": str(hpaths.KICAD_DEMOS), "steps": steps}
    (hpaths.RUN_DIR / "prepare_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    say(f"prepare: {'all steps passed' if report['ok'] else 'FAILED'}; report in "
        f"{hpaths.RUN_DIR / 'prepare_report.json'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
