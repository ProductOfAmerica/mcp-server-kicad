"""Build the persistent perf fixtures (FIXTURES/probeC/perf/, git-ignored) with code copied from
perf_prepare.py, perf_augment.py, perf_k200.py and vp_fixtures.py, and re-derive the pin selection
(pins.rederived.json beside the fixtures; the committed pins.json beside this file is never written).
Every result is cross-checked, read-only, against what this repository commits: pins.json (the pin
lists) and baselines/fixtures_expected.json (file hashes, connector origins and the free-pin x0,
taken from the scratch run whose figures the ADR cites). Repathed and standalone copies must be
byte-identical (sha256); the files place_component wrote must be equal after UUID normalisation
(the first 20 hex digits of the sha256 with every UUID replaced by U); connector origins and x0
equal; the pin lists equal to the committed ones; the vp_stock sweep counts must hold. kicad-cli
checks that every new connector pin is alone on its net and that no stock net changes. No records
are written; build_report.json goes into the fixtures directory. Exit status 1 on any mismatch.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402,F401

import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

import pfix as F  # noqa: E402

EXPECTED = json.loads((hpaths.BASELINES / "fixtures_expected.json").read_text(encoding="utf-8"))
EXP_FILES = EXPECTED["probeC_perf"]
EXP_REPORT = EXPECTED["probeC_perf_report"]
COMMITTED_PINS = json.loads((F.HERE / "pins.json").read_text(encoding="utf-8"))
UUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
(F.FIX / "probe").mkdir(parents=True, exist_ok=True)
(F.FIX / "verify").mkdir(parents=True, exist_ok=True)
BAD = []  # every mismatch against the expectations, one readable line each


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def norm(p):
    """check_bases' rule: sha256 of the bytes with every UUID replaced by U, first 20 hex digits."""
    return hashlib.sha256(UUID_RE.sub(b"U", Path(p).read_bytes())).hexdigest()[:20]


def same_file(p, rel):
    """Does the built file equal the expectation recorded for fixtures/<rel>?"""
    exp = EXP_FILES.get(rel)
    if exp is None:
        BAD.append(f"{rel}: no entry in baselines/fixtures_expected.json (probeC_perf)")
        return False
    got, want = (sha(p), exp["sha256"]) if "sha256" in exp else (norm(p), exp["norm"])
    if got != want:
        BAD.append(f"{rel}: built {got}, expected {want}")
    return got == want


def expected(*path):
    """The expectation at path in probeC_perf_report (None, and a mismatch, when it is missing)."""
    cur = EXP_REPORT
    try:
        for k in path:
            cur = cur[k]
    except (KeyError, TypeError):
        BAD.append("no entry in baselines/fixtures_expected.json probeC_perf_report: " + "/".join(path))
        return None
    return cur


def jsonable(x):
    return json.loads(json.dumps(x))


def check_new(before, after, new):
    """(stock nets unchanged by node set and name, new pins each alone, missing new pins)."""
    bset = {(n, v) for _c, n, v in before}
    stock_ok = all(any(v2 == v and n2 == n for _c, n2, v2 in after) for n, v in bset)
    alone = {x for _c, _n, v in after if len(v) == 1 for x in v}
    seen = {x for _c, _n, v in after for x in v}
    return stock_ok, set(new) <= alone, sorted(set(new) - seen)


report = {"probe": {}, "verify": {}}
pins = {"prepare": {}, "stock_pins": {}, "pick_pins": {}}

# ---------------------------------------------------------------- probe fixtures
for name, rel in F.BIG.items():
    rp = F.FIX / "probe" / f"{name}.kicad_sch"
    F.repath(F.STOCK / rel, rp)
    same = same_file(rp, f"probe/{name}.kicad_sch")
    nets = F.netlist(rp)
    prep = F.prepare(rp, nets)
    pins["prepare"][name] = prep
    o = COMMITTED_PINS["prepare"][name]
    key = lambda lst: [(q["ref"], q["pin"]) for q in lst]  # noqa: E731
    pkey = lambda lst: [((q["a"]["ref"], q["a"]["pin"]), (q["b"]["ref"], q["b"]["pin"])) for q in lst]  # noqa: E731
    prep_same = (key(prep["free"]) == key(o["free"]) and key(prep["unnamed"]) == key(o["unnamed"])
                 and pkey(prep["pairs"]) == pkey(o["pairs"]))
    aug = F.FIX / "probe" / f"{name}.aug.kicad_sch"
    info = F.build_aug(rp, aug)
    aug_same = same_file(aug, f"probe/{name}.aug.kicad_sch")
    oa = expected("probe", name, "aug")
    pos_same = oa is not None and all(jsonable(info).get(k) == v for k, v in oa.items())
    new = [("J9001", str(k)) for k in range(1, 51)] + [(r, str(k)) for r in ("J9002", "J9003")
                                                         for k in range(1, 11)]
    stock_ok, alone, missing = check_new(nets, F.netlist(aug), new)
    report["probe"][name] = {"repath_byte_identical_to_expected": same, "nets": len(nets),
                             "free": len(prep["free"]), "unnamed": len(prep["unnamed"]),
                             "pairs": len(prep["pairs"]), "excluded": prep["excluded"],
                             "prepare_equal_to_committed": prep_same, "aug": info,
                             "aug_equal_to_expected": aug_same,
                             "aug_positions_equal_to_expected": pos_same,
                             "aug_stock_nets_unchanged": stock_ok, "aug_new_pins_alone": alone,
                             "aug_new_pins_missing": missing}
    print(name, json.dumps(report["probe"][name]), flush=True)

k200 = F.FIX / "probe" / "tinytapeout-demo.k200.kicad_sch"
placed = F.build_k200(F.FIX / "probe" / "tinytapeout-demo.aug.kicad_sch", k200)
k200_file = same_file(k200, "probe/tinytapeout-demo.k200.kicad_sch")
ok200 = expected("probe", "k200", "placed")
k200_same = ok200 is not None and jsonable(placed) == ok200
before_aug = F.netlist(F.FIX / "probe" / "tinytapeout-demo.aug.kicad_sch")
new200 = [(r, str(i)) for r in ("J9004", "J9005", "J9006") for i in range(1, 51)]
stock_ok, alone, missing = check_new(before_aug, F.netlist(k200), new200)
report["probe"]["k200"] = {"placed": placed, "equal_to_expected": k200_file,
                           "positions_equal_to_expected": k200_same,
                           "stock_nets_unchanged": stock_ok, "new_pins_alone": alone,
                           "missing": missing}
print("k200", json.dumps(report["probe"]["k200"]), flush=True)

# ---------------------------------------------------------------- verifier fixtures
SWEEP_COUNTS = {"ampli_ht": 43, "tinytapeout-demo": 53, "fpga-hp-banks": 14}
for name, rel in F.SHEETS.items():
    base = F.FIX / "verify" / f"{name}.base.kicad_sch"
    F.standalone_copy(F.STOCK / rel, base)
    same = same_file(base, f"verify/{name}.base.kicad_sch")
    nb = F.netlist(base)
    row = {"base_byte_identical_to_expected": same, "nets": len(nb)}
    variants = [("free", ["J8001"])]
    if name == "tinytapeout-demo":
        variants.append(("free200", ["J8001", "J8002", "J8003", "J8004"]))
    for tag, refs in variants:
        dst = F.FIX / "verify" / f"{name}.{tag}.kicad_sch"
        x0 = F.build_free(rel, dst, refs)
        new = {(r, str(k)) for r in refs for k in range(1, 51)}
        stock_ok, alone, missing = check_new(nb, F.netlist(dst), new)
        ox0 = expected("verify", name, tag, "x0")
        row[tag] = {"x0": x0, "x0_equal_to_expected": ox0 is not None and abs(x0 - ox0) < 1e-9,
                    "equal_to_expected": same_file(dst, f"verify/{name}.{tag}.kicad_sch"),
                    "stock_nets_unchanged": stock_ok, "new_pins_alone": alone, "missing": missing}
    if name in SWEEP_COUNTS:
        sp = F.stock_pins(base, nb)
        pins["stock_pins"][name] = sp
        row["stock_pins"] = len(sp)
        row["stock_pins_count_equal_to_vp_stock"] = len(sp) == SWEEP_COUNTS[name]
    pins["pick_pins"][name] = F.pick_pins(str(F.STOCK / rel), 50)
    row["pick_pins"] = len(pins["pick_pins"][name])
    report["verify"][name] = row
    print(name, json.dumps(row), flush=True)

# ---------------------------------------------------------------- pins, report, verdict
(F.FIX / "pins.rederived.json").write_text(json.dumps(pins, indent=1), encoding="utf-8")
report["pins_rederived_equal_to_committed"] = jsonable(pins) == COMMITTED_PINS
(F.FIX / "build_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

bad_flags = []


def walk(x, path):
    """Every boolean in the report must be true and every 'missing' list empty."""
    if isinstance(x, dict):
        for k, v in x.items():
            walk(v, f"{path}.{k}")
    elif isinstance(x, bool):
        if not x:
            bad_flags.append(path)
    elif isinstance(x, list) and path.endswith("missing") and x:
        bad_flags.append(f"{path}={x}")


walk(report, "report")
if BAD or bad_flags:
    print("MISMATCH", flush=True)
    for line in BAD:
        print("  ", line, flush=True)
    for line in bad_flags:
        print("   false or non-empty:", line, flush=True)
    sys.exit(1)
print("done", flush=True)
