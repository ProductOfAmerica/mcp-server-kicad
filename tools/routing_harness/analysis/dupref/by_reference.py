"""Follow-up 3: lift.json's Hb dup_ref calls (63 in follow-up 3's data) grouped by the shared reference
H-C1 named (from the follow-up 2 refusal message), with Hb2's outcome per group.
Run: uv run python tools/routing_harness/analysis/dupref/by_reference.py --old RUN:Hb --new RUN:Hb2
         > <new run>/analysis/by_reference.out
It takes the options of lift.py, which it runs first (see there)."""
import glob
import json
import re
from collections import Counter, defaultdict
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import lift as L  # noqa: E402  (re-runs lift.py's loading and its --old/--new options; prints its own summary first)

groups = defaultdict(Counter)
for batch in ("probeA", "probeB", "probeC", "llm", "layouts", "demo"):
    old = L.load(batch, L.OLD_ROOT, L.OLD_D)
    new = L.load(batch, L.NEW_ROOT, L.NEW_D)
    for k, r in old.items():
        if r.get("status") != "REFUSED" or "dup_ref" not in (r.get("codes") or []):
            continue
        refs = sorted(set(re.findall(r"reference (\S+) is carried by", r.get("msg") or r.get("msg_head") or "")))
        where = "PI-15 (true duplicates)" if r.get("case") == "PI-15" else "+".join(refs) or "?"
        groups[(where, batch)][L.outcome(new.get(k))] += 1
print("\n== by shared reference")
for (where, batch), c in sorted(groups.items()):
    print(f"{where:28s} {batch:8s} {sum(c.values()):3d} -> {dict(c)}")
