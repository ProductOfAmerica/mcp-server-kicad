"""Regression test for judge3.named_merge (fix 2026-10-03). Synthetic netlists, no kicad-cli."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import tempfile  # noqa: E402

# judge3 and harness make a temp directory when imported: keep it under the run directory.
_tmp = hpaths.suite_out("selftest") / "tmp"
_tmp.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_tmp)

import judge3 as J  # noqa: E402

F = frozenset


def nm(before, after, groups, n_nodes=None):
    return J.judge(before, after, groups, None, n_nodes=n_nodes, n_after=set().union(*groups),
                   status="OK")["named_merge"]


# 1 auto name holding '/' (pin PB7/PB8) joined to an existing N: requested, not a named merge
b = [("1", "unconnected-(U1-PB7/PB8-Pad1)", "", F({("U1", "PB7")})), ("2", "/N", "", F({("R1", "1")}))]
a = [("1", "/N", "", F({("U1", "PB7"), ("R1", "1")}))]
r1 = nm(b, a, [{("U1", "PB7")}], n_nodes={("R1", "1")})
# 2 the same with the auto name under a sheet path
b2 = [("1", "/kid/unconnected-(U1-PB7/PB8-Pad1)", "", F({("U1", "PB7")})), ("2", "/kid/N", "", F({("R1", "1")}))]
a2 = [("1", "/kid/N", "", F({("U1", "PB7"), ("R1", "1")}))]
r2 = nm(b2, a2, [{("U1", "PB7")}], n_nodes={("R1", "1")})
# 3 two local nets of the same text on two sheets joined: a named merge
b3 = [("1", "/A/X", "", F({("R1", "1")})), ("2", "/B/X", "", F({("R2", "1")}))]
a3 = [("1", "/A/X", "", F({("R1", "1"), ("R2", "1")}))]
r3 = nm(b3, a3, [{("R1", "1")}], n_nodes={("R2", "1")})
# 4 +5V into GND
b4 = [("1", "+5V", "", F({("R1", "1")})), ("2", "GND", "", F({("R2", "1")}))]
a4 = [("1", "GND", "", F({("R1", "1"), ("R2", "1")}))]
r4 = nm(b4, a4, [{("R1", "1")}], n_nodes={("R2", "1")})
# 5 two auto nets and one N
b5 = [("1", "Net-(R1-Pad1)", "", F({("R1", "1")})), ("2", "/kid/Net-(R2-Pad1)", "", F({("R2", "1")})),
      ("3", "/N", "", F({("R3", "1")}))]
a5 = [("1", "/N", "", F({("R1", "1"), ("R2", "1"), ("R3", "1")}))]
r5 = nm(b5, a5, [{("R1", "1"), ("R2", "1")}], n_nodes={("R3", "1")})
for tag, got, want in (("1 auto with slash", r1, False), ("2 auto with path and slash", r2, False),
                       ("3 /A/X + /B/X", r3, True), ("4 +5V + GND", r4, True),
                       ("5 two auto + N", r5, False)):
    ok = bool(got) == want
    print(f"{'PASS' if ok else 'FAIL'} {tag}: named_merge={got}")
    assert ok
print("all pass")
