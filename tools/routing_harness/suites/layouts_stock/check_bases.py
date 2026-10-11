"""Compare each base file built here (FIXTURES/layouts_stock/bases, made by runner.py --only none)
with follow-up 1's pre-call state of the first call of the same layout / star / junction fixture,
UUID-normalised. The expected hashes are baselines/layouts_followup1_bases.json, taken from
follow-up 1's records (M2, call_index 1, first attempt, no experiment key; pr57pt/coarse/rerun/
layouts_stock/records.jsonl) by baselines/derive.py. Equal hashes = the fixture builders reproduce
follow-up 1's bases. Prints the result as JSON; exits 1 unless all 67 are equal.
Run: uv run python tools/routing_harness/suites/layouts_stock/check_bases.py"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import hashlib, json, re  # noqa: E402
UUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
nh = lambda b: hashlib.sha256(UUID_RE.sub(b"U", b)).hexdigest()[:20]
BASES = hpaths.FIXTURES / "layouts_stock" / "bases"
if not BASES.is_dir():
    sys.exit(f"no bases under {BASES}: run suites/layouts_stock/runner.py --only none first "
             "(prepare.py does)")
# {"<sample>/<name>": pre_norm}, e.g. "gen/L00_attiny25", "star/star00", "jct/J1_pin_first"
fu1 = json.loads((hpaths.BASELINES / "layouts_followup1_bases.json").read_text(encoding="utf-8"))
res = {"equal": 0, "differ": [], "missing": []}
seen = set()
for p in sorted(BASES.rglob("*.kicad_sch")):
    k = (p.parent.parent.name, p.stem)
    key = f"{k[0]}/{k[1]}"
    seen.add(key)
    if key not in fu1:
        res["missing"].append(k); continue
    if fu1[key] == nh(p.read_bytes()):
        res["equal"] += 1
    else:
        res["differ"].append((k, [fu1[key]], nh(p.read_bytes())))
print(json.dumps(res, indent=1))
ok = res["equal"] == len(fu1) == 67 and not res["differ"] and not res["missing"]
if not ok:
    print(f"expected 67 equal bases, got {res['equal']}; no base file for: {sorted(set(fu1) - seen)}",
          file=sys.stderr)
sys.exit(0 if ok else 1)
