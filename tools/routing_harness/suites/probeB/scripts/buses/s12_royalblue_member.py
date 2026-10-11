# probeB copy of pr57pt/probe-buses/s12_royalblue_member.py (header swapped to the probeB shims; fixture code unchanged).
"""S12: KiCad's own royalblue54L_feather demo (copied), sub-sheet sch/Connectors.kicad_sch. There the
hierarchical bus label 'UART{TX, RX}' sits exactly on the bus point of the UART.TX member entry
(c03 census), and the member wire to the connector carries local label 'UART.TX'.

Fixture: the whole project copied; ONLY the 'UART.TX' label is removed from Connectors.kicad_sch
(a member wire not yet named, the work-in-progress state). Judged on the ROOT export.

s12a  wire_pins_to_net(<connector pin on that member wire>, 'UART.TX')   expected OK (restores the
      member connection that the original demo has)
s12b  wire_pins_to_net(same pin, 'UART{TX, RX}')                          expected REFUSED (bus
      syntax; the pin is not on that bus in KiCad)
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s12_royalblue_member", __file__)
from bl import P, _cst, dump, nets_coded, pins_arg, scenario  # noqa: E402

SRC = hpaths.KICAD_DEMOS / "royalblue54L_feather"
ROOT = "RoyalBlue54L-Feather.kicad_sch"
SUB = "sch/Connectors.kicad_sch"


def fixture(tag):
    d = Path(tempfile.mkdtemp(prefix=f"s12_{tag}_")) / "rb"
    shutil.copytree(SRC, d)
    sub = d / SUB
    tree = _cst.parse(sub.read_bytes())
    root = tree.lists[0]
    gone = [n for n in root.find_all("label") if n.atoms[1].text == "UART.TX"]
    assert len(gone) == 1, gone
    root.remove_child(gone[0])
    sub.write_bytes(_cst.serialize(tree))
    return d


# which pin sits at the far end of the UART.TX member wire (68.58, 124.46)?
orig = Path(tempfile.mkdtemp(prefix="s12_orig_")) / "rb"
shutil.copytree(SRC, orig)
sub_root = _cst.parse((orig / SUB).read_bytes()).lists[0]
m = P._Model(sub_root)
target = None
for idx, k in enumerate(m.kind):
    if k == "pin" and m.data[idx]["pt"] == (P._iu(68.58), P._iu(124.46)):
        target = (m.data[idx]["ref"], m.data[idx]["number"])
print("target pin at (68.58, 124.46):", target)
n0 = nets_coded(str(orig / ROOT))
print("original demo, target's net:", [(name, sorted(v)) for _, name, v in n0 if target in v])
d = fixture("probe")
n1 = nets_coded(str(d / ROOT))
print("after removing label UART.TX, target's net:", [(name, sorted(v)) for _, name, v in n1 if target in v])

for sid, N, exp in (("s12a", "UART.TX", "OK"), ("s12b", "UART{TX, RX}", "REFUSED")):
    d = fixture(sid)
    scenario(sid, f"royalblue Connectors, UART.TX member label removed: {target[0]}:{target[1]} -> {N!r}",
             str(d / ROOT), ("wire_pins_to_net", dict(pins=pins_arg(target), label_text=N)),
             edit_rel=SUB, root_rel=ROOT, targets=[target], N=N, expect=exp,
             n_alias=(f"/Connectors/{N}",))

dump("s12_royalblue_member")
