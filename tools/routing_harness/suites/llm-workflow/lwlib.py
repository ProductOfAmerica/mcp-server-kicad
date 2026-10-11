"""lwlib (follow-up 2): helpers for the llm-workflow re-run of designs M2, H, Hb and E (on top of
rlib.py in this directory). Copied from pr57pt/coarse/rerun/probeC/llm-workflow/lwlib.py; changes:
  - K and the repo-tool pair fallbacks are gone (pair_fallbacks deleted);
  - refusal lines and codes are parsed for [M:], [K:], [H:] and [E:] codes;
  - reason_kind's "geometry" class (the adaptive policy's stub-5.08 retry) is the touch-rule
    failure of each design: [M:touch], [H:touch] (Hb the same) and E's [E:blocked] ("no clear stub
    and no clear pin end for a label"), which is E's only "no geometry fits" code;
  - each Seq call records input_key (uuid-blanked digest of the pre-call state), fixture and
    attempt ("first" for a plan's own call, else "retry", "fallback" or "replay"), and keeps the
    pre/post fixture of a wrong write (rlib.keep_wrong).

Seq: one design's own evolving copy of a fixture, for the branching policies of the original
scripts (connect_pins refused -> wire_pins_to_net fallback, adaptive retries, conditional replays,
remove_no_connect remedies). Each call is judged against the state just before it with rlib's
Spec / judged_call. Records of a sequence: variant = "<scenario>:<step_label>" (one variant per
labelled call, so the designs' records of the same call pair by (case, variant)); "step" is the
design's own running call index (it can differ between designs once a policy branches).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import hpaths  # noqa: E402  (an entry script has already put it on sys.path)
import rlib as R
from rlib import H  # noqa: F401
from mcp_server_kicad import _cst
from mcp_server_kicad import schematic as SCH

CAT = "llm-workflow"
PROBE = "probe-llm-workflow/"
VERIFY = "verify-probe-llm-workflow/"
SYM = Path(hpaths.KICAD_SYMBOLS)
DEMOS = Path(hpaths.KICAD_DEMOS)
M2 = R.M2


# ----------------------------------------------------------------------------- judge memo
# Pure caching of two deterministic judge3 functions, keyed on content with uuid values blanked
# (no reading of connectivity depends on a uuid value; the same normalisation as ctlib._norm):
#   judge3.nets_full(path)        kicad-cli 9.0.8 netlist of path's directory tree
#   judge3.gui_tree_copy(root)    the GUI-proxied copy of root's directory tree
# The judging logic (judge, snapshot2, assess2) is untouched; a cache hit returns exactly what a
# fresh run on byte-equal-modulo-uuid input returns.
import hashlib  # noqa: E402

_NETS: dict = {}
_GUI: dict = {}
_ORIG_NETS = R.J.nets_full
_ORIG_GUI = R.J.gui_tree_copy
_GUI_DIR = R.TMPDIR / "guicache"
_UUID = re.compile(rb'\(uuid "[^"]*"\)')
MEMO_STATS = {"nets_hit": 0, "nets_miss": 0, "gui_hit": 0, "gui_miss": 0}


def _tree_key(path) -> str:
    path = Path(path)
    d = path.parent
    h = hashlib.sha256()
    for f in sorted(d.rglob("*")):
        if f.is_file():
            b = f.read_bytes()
            if f.suffix == ".kicad_sch":
                b = _UUID.sub(b'(uuid "")', b)
            h.update(str(f.relative_to(d)).encode() + b"\0" + len(b).to_bytes(8, "little"))
            h.update(b)
    h.update(b"\0root:" + path.name.encode())
    return h.hexdigest()


def _memo_nets_full(path):
    key = _tree_key(path)
    if key in _NETS:
        MEMO_STATS["nets_hit"] += 1
        return _NETS[key]
    MEMO_STATS["nets_miss"] += 1
    res = _ORIG_NETS(path)
    if len(_NETS) > 2000:
        _NETS.clear()
    _NETS[key] = res
    return res


def _memo_gui_tree_copy(root_path):
    key = _tree_key(root_path)
    hit = _GUI.get(key)
    if hit is not None and hit.exists():
        MEMO_STATS["gui_hit"] += 1
        return hit
    MEMO_STATS["gui_miss"] += 1
    out = Path(_ORIG_GUI(root_path))
    if len(_GUI) > 150:
        shutil.rmtree(_GUI_DIR, ignore_errors=True)
        _GUI.clear()
    dst = _GUI_DIR / key[:20]
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(out.parent, dst)
    _GUI[key] = dst / out.name
    return _GUI[key]


R.J.nets_full = _memo_nets_full
R.J.gui_tree_copy = _memo_gui_tree_copy


# ----------------------------------------------------------------------------- sequences


_SNAPS: dict = {}  # normalised pre-call state + request -> snapshot (reused across designs)


_norm_state = R.norm_state


def cached_snapshot(spec, root, edited, d: Path):
    key = (_norm_state(d), str(Path(root).relative_to(d)), str(Path(edited).relative_to(d)),
           spec.tool, repr(spec.N), repr(getattr(spec, "power", None)),
           repr(getattr(spec, "ground", None)))
    if key not in _SNAPS:
        if len(_SNAPS) > 64:
            _SNAPS.clear()
        _SNAPS[key] = spec.snapshot(root, edited)
        R.wipe_judge_scratch()
    return _SNAPS[key]


class Seq:
    """One design's evolving copy of a fixture; each call judged against the state before it."""

    def __init__(self, case, scenario, design, base_root, base_edited=None, also=(), script=None,
                 note=""):
        self.case, self.scenario, self.design = case, scenario, design
        self.also, self.script, self.note = list(also), script, note
        base_root = Path(base_root).resolve()
        base_edited = Path(base_edited).resolve() if base_edited else base_root
        self.base_dir = base_root.parent
        self.dir = R.copy_tree(self.base_dir, f"{self.base_dir.name}__lw_{design}")
        self.root = R._rel(base_root, self.base_dir, self.dir)
        self.edited = R._rel(base_edited, self.base_dir, self.dir)
        self.step = 0
        self.pre: dict = {}
        self.recs: dict = {}
        self.full: dict = {}
        self.labels: list = []
        self._seen: dict = {}

    def map(self, base_path) -> Path:
        """A path in the base fixture -> the same file in this design's copy."""
        return R._rel(base_path, self.base_dir, self.dir)

    def call(self, tool, kw, label, edited=None, extra=None, attempt="first"):
        e2 = self.edited if edited is None else self.map(edited)
        k = self._seen.get(label, 0)
        self._seen[label] = k + 1
        if k:
            label = f"{label} [{k + 1}]"
        spec = R.Spec(tool, kw, e2)
        pre = R.copy_tree(self.dir, f"{self.dir.name}__pre{self.step}")
        snap = cached_snapshot(spec, self.root, e2, self.dir)
        rec = dict(case=self.case, variant=f"{self.scenario}:{label}", tool=tool,
                   design=self.design, kind="seq", step=self.step, step_label=label,
                   scenario=self.scenario, also=self.also,
                   N=spec.N if spec.cap is None else [spec.power, spec.ground], pads=spec.pads,
                   note=self.note, kw=R._kwtext(kw), fixture=self.base_dir.name,
                   input_key=R.norm_state(self.dir), attempt=attempt)
        if self.script:
            rec["script"] = self.script
        if extra:
            rec.update(extra)
        _st, full = R.judged_call(getattr(R.IMPLS[self.design], tool), kw, spec, snap, self.root,
                                  e2, self.dir, rec, pre_dir=pre)
        R.emit(rec)
        R._line(rec)
        self.pre[label] = (pre, spec, tool, kw, e2.relative_to(self.dir))
        self.recs[label] = rec
        self.full[label] = full
        self.labels.append(label)
        self.step += 1
        return rec

    def close(self):
        for pre, *_ in self.pre.values():
            shutil.rmtree(pre, ignore_errors=True)
        shutil.rmtree(self.dir, ignore_errors=True)


def close_all(seqs: dict):
    for s in seqs.values():
        s.close()


def header(text):
    print(f"== {text}", flush=True)


# ----------------------------------------------------------------------------- message parsing


_REFLINE = re.compile(r"^- REFUSED ((?:\[[MKHE]:\w+\]\s*)+)(\S+?:\S+?): (.*)$")


def refused_tags(msg: str):
    """[(tag, codes, reason)] named by a wire_pins_to_net refusal (one per REFUSED line). Every
    design puts its reason codes between REFUSED and the tag."""
    out = []
    for line in (msg or "").splitlines():
        m = _REFLINE.match(line)
        if m:
            out.append((m.group(2), re.findall(r"\[[MKHE]:(\w+)\]", m.group(1)), m.group(3)))
    return out


def reason_kind(codes, reason):
    """The original run_designs.reason_kind classes, read from the reason codes (the original
    matched mine_impl's text; K's texts differ, e.g. its [K:names] text has no 'already on')."""
    r = (reason or "").lower()
    if "matches several pads" in r:
        return "ambiguous-name"
    cs = set(codes)
    if cs & {"nc_type", "nc_flag"}:
        return "nc"
    if "names" in cs:
        return "name"
    if cs & {"port", "unknown", "sheet_pin", "text_var"}:
        return "port/unknown"
    if "ambiguous" in cs:
        return "cross-version"
    if "resolve" in cs:
        return "lookup"
    if cs & {"touch", "blocked"}:
        return "geometry"
    return "other:" + ",".join(sorted(cs))


# ----------------------------------------------------------------------------- netlists


def nets(p):
    """kicad-cli 9.0.8 netlist [(name, frozenset(nodes))] via judge3.nets_full (scratch in J.TMP)."""
    res = R.J.nets_full(p)
    if res is None:
        return None
    return [(name, v) for _c, name, _k, v in res]


AUTO = ("Net-(", "unconnected-(")


def strip(n: str) -> str:
    return n[1:] if n.startswith("/") else n


def strong(n: str) -> bool:
    return bool(n) and not strip(n).startswith(AUTO)


def pin_numbers(p, ref, ptxt):
    """llmlib.pin_numbers on M2's model (identical model code): the exact number if a pin has it,
    else every pin number whose primary name matches."""
    root = _cst.parse(Path(p).read_bytes()).lists[0]
    m = M2._Model(root)
    syms = [s for s in m.syms if s.ref == ref and s.lib is not None]
    nums, byname = set(), set()
    for s in syms:
        for sub in s.lib.find_all("symbol"):
            for pn in sub.find_all("pin"):
                num = pn.find("number").atoms[1].text
                nam = pn.find("name").atoms[1].text
                nums.add(num)
                if nam == ptxt:
                    byname.add(num)
    if ptxt in nums:
        return [ptxt]
    return sorted(byname)


# ----------------------------------------------------------------------------- stock sheets


def repath_realsheets(src, dst):
    """Copy of proto/mine_realsheets.repath (used by demo_probe.py)."""
    tree = _cst.parse(Path(src).read_bytes())
    root = tree.lists[0]
    u = root.find("uuid").atoms[1].text
    for s in root.find_all("symbol"):
        inst = s.find("instances")
        if inst is None:
            continue
        paths = [pth for pj in inst.find_all("project") for pth in pj.find_all("path")]
        for i, pth in enumerate(paths):
            pth.atoms[1].set_text(f"/{u}" if i == 0 else "/00000000-0000-0000-0000-00000000dead")
    Path(dst).write_bytes(_cst.serialize(tree))
    return root


def repath_vlib(src, dst):
    """Copy of verify-probe-llm-workflow/vlib.repath (used by v_lw03_mirror.py)."""
    tree = _cst.parse(Path(src).read_bytes())
    root = tree.lists[0]
    u = root.find("uuid").atoms[1].text
    for s in root.find_all("symbol"):
        inst = s.find("instances")
        if inst is None:
            continue
        first = True
        for proj in inst.find_all("project"):
            for pth in proj.find_all("path"):
                pth.atoms[1].set_text(f"/{u}" if first else "/00000000-0000-0000-0000-0000000000ff")
                first = False
    Path(dst).write_bytes(_cst.serialize(tree))
    return dst


def fixture_dir(name) -> Path:
    d = Path(H.BASE) / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def copy_file_fixture(src, name, fname=None) -> Path:
    """A fresh fixture dir holding a copy of one schematic file. Returns the new path."""
    d = fixture_dir(name)
    dst = d / (fname or Path(src).name)
    shutil.copy2(src, dst)
    return dst


# ----------------------------------------------------------------------------- gen_layouts copies


_lib_cache: dict = {}


def lib_symbol(lib_id):
    lib, name = lib_id.split(":")
    if lib not in _lib_cache:
        root = _cst.parse((SYM / f"{lib}.kicad_sym").read_bytes()).lists[0]
        _lib_cache[lib] = {s.atoms[1].text: s for s in root.find_all("symbol")}
    return _lib_cache[lib][name]


def extent_points(lib_id):
    """Copy of gen_layouts.extent_points."""
    s = lib_symbol(lib_id)
    pts = []
    for sub in s.find_all("symbol"):
        ids = sub.atoms[1].text.rsplit("_", 2)
        if len(ids) == 3 and (ids[1] not in ("0", "1") or ids[2] not in ("0", "1")):
            continue
        for p in sub.find_all("pin"):
            at = p.find("at")
            x, y = float(at.atoms[1].text), float(at.atoms[2].text)
            a = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
            ln = float(p.find("length").atoms[1].text)
            dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[int(a) % 360]
            pts += [(x, y), (x + dx * ln, y + dy * ln)]
        for r in sub.find_all("rectangle"):
            for k in ("start", "end"):
                n = r.find(k)
                pts.append((float(n.atoms[1].text), float(n.atoms[2].text)))
        for pl in sub.find_all("polyline"):
            for xy in pl.find("pts").find_all("xy"):
                pts.append((float(xy.atoms[1].text), float(xy.atoms[2].text)))
        for c in sub.find_all("circle"):
            cx, cy = (float(v.text) for v in c.find("center").atoms[1:3])
            rr = float(c.find("radius").atoms[1].text)
            pts += [(cx - rr, cy - rr), (cx + rr, cy + rr)]
    return pts


def bbox(lib_id, x, y, rot):
    """Copy of gen_layouts.bbox (M._ROT / M._apply are m2_impl's identical tables)."""
    t = M2._ROT[rot]
    xs, ys = [], []
    for lx, ly in extent_points(lib_id):
        dx, dy = M2._apply(t, lx, -ly)
        xs.append(x + dx)
        ys.append(y + dy)
    return min(xs), min(ys), max(xs), max(ys)


def overlaps(a, b, margin=1.27):
    """Copy of gen_layouts.overlaps."""
    return not (a[2] + margin <= b[0] or b[2] + margin <= a[0] or a[3] + margin <= b[1]
                or b[3] + margin <= a[1])


def pins_kw(pins):
    return [{"reference": r, "pin": x} for r, x in pins]


def remove_nc(p, ref, num):
    """The repo's remove_no_connect (an LLM remedy step), as demo_probe.remove_nc."""
    try:
        return SCH.remove_no_connect(reference=ref, pin_name=num, schematic_path=str(p))
    except Exception as e:  # noqa: BLE001
        return f"remove_no_connect FAILED: {type(e).__name__}: {e}"
