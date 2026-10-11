"""Child for the composite rerun: one auto_place_decoupling_cap call on one design, outcome as JSON.

argv: design (a core/registry.py name: X, Hb2, Hb, H, M2, E) schematic_path kwargs_json_file mode
  mode "plain"  : no hooks (v02_child.py); the parent acts on the files from outside.
  mode "log"    : print 'write k: name' after every completed _atomic_write (c04 dry run).
  mode "exit:K" : as log, and os._exit(9) right after the K-th completed write (c04_child.py).
TMP/TEMP are redirected under the run's probeC/tmp (hpaths.suite_out) before the designs are imported.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

sys.dont_write_bytecode = True
OUT = hpaths.suite_out("probeC")
T = OUT / "tmp" / f"cmpchild_{os.getpid()}"
T.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(T)
tempfile.tempdir = str(T)

design, sch, kwfile, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
kw = json.loads(Path(kwfile).read_text(encoding="utf-8"))

import e_impl as E  # noqa: E402
import h_impl as Hd  # noqa: E402
import hb_impl as Hb  # noqa: E402
import m2_impl as M2  # noqa: E402
import registry  # noqa: E402
from mcp_server_kicad import _shared  # noqa: E402

mod = registry.load(design)
assert Hb.MODULE.CROSS_BAN is True and Hd.CROSS_BAN is False
if mode != "plain":
    kill_after = int(mode.split(":")[1]) if mode.startswith("exit:") else 0
    real = _shared._atomic_write
    n = {"k": 0}

    def aw(path, data):
        real(path, data)
        n["k"] += 1
        print(f"write {n['k']}: {os.path.basename(str(path))}", flush=True)
        if n["k"] == kill_after:
            shutil.rmtree(T, ignore_errors=True)
            os._exit(9)

    _shared._atomic_write = aw

print("ready", flush=True)
try:
    msg = str(mod.auto_place_decoupling_cap(schematic_path=sch, **kw))
    st = "NOOP" if msg.lower().startswith("no change") else "OK"
except BaseException as e:  # noqa: BLE001
    cls = type(e).__name__
    st = "REFUSED" if cls == "ToolError" else cls
    ctx = e.__context__
    extra = f" [context: {type(ctx).__name__}: {str(ctx)[:200]}]" if ctx else ""
    msg = f"{cls}: {e}{extra}"
print(json.dumps({"status": st, "msg": msg}), flush=True)
shutil.rmtree(T, ignore_errors=True)
