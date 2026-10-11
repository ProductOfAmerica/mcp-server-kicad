# probeB copy of pr57pt/probe-hierarchy/h12_multichannel_demo.py.
# The original read RUNS/h12_multichannel/orig, a byte-identical copy of KiCad 9.0.8's demo
# 'multichannel' (checked: channel_strip, multichannel_mixer .kicad_sch/.kicad_pro/.kicad_sym equal);
# this copy makes that copy itself. Calls unchanged.
"""H12: KiCad's own multichannel demo (copied; 4 instances CH1..CH4 of channel_strip.kicad_sch,
each with sheet pin OUT; the four OUT pins meet on the root at R3:1 / IC1:2, net '/CH1/OUT').

12a root:  wire_pins_to_net([R3:1], 'MIX')     fresh name on a port-only-named net
12b root:  wire_pins_to_net([R3:1], 'GND')     existing power name
12c child: wire_pins_to_net([C13:2], '+12V')   C13:2 sits on hier label OUT (CH1 refs)
12d child: wire_pins_to_net([C5:1], 'vbias')   a 2-pin unnamed net; vbias is a power Value on the child
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h12_multichannel_demo", __file__)
import shutil  # noqa: E402

from hlib import DEMOS, HERE, RUNS, Log, netlist, run, show  # noqa: E402

log = Log(HERE / "h12_multichannel_demo.out")
orig = RUNS / "h12_multichannel" / "orig"
shutil.copytree(DEMOS / "multichannel", orig)
ROOT = "multichannel_mixer.kicad_sch"
outs = [("C13", "2"), ("C22", "2"), ("C31", "2"), ("C40", "2"), ("R3", "1"), ("IC1", "2")]
run(log, "h12a_root_MIX", orig, ROOT, ROOT, "wire_pins_to_net",
    dict(pins=[{"reference": "R3", "pin": "1"}], label_text="MIX"), allowed=[("R3", "1")],
    ideal="policy: the net is named only by the 4 OUT port chains; a rename is the requested naming",
    pin_nodes=outs[:1] + outs[4:])
run(log, "h12b_root_GND", orig, ROOT, ROOT, "wire_pins_to_net",
    dict(pins=[{"reference": "R3", "pin": "1"}], label_text="GND"), allowed=[("R3", "1"), ("IC1", "4")],
    ideal="policy: literally requested (summing node to GND); M refuses by port policy",
    pin_nodes=outs[:1] + outs[4:])
run(log, "h12c_child_OUT_pin", orig, ROOT, "channel_strip.kicad_sch", "wire_pins_to_net",
    dict(pins=[{"reference": "C13", "pin": "2"}], label_text="+12V"),
    allowed=[("C13", "2"), ("C22", "2"), ("C31", "2"), ("C40", "2")],
    ideal="refuse: C13:2 is on hier label OUT; +12V would short all four outputs to the rail",
    pin_nodes=outs)
run(log, "h12d_child_unnamed_vbias", orig, ROOT, "channel_strip.kicad_sch", "wire_pins_to_net",
    dict(pins=[{"reference": "C5", "pin": "1"}], label_text="vbias"),
    allowed=[("C5", "1"), ("C14", "1"), ("C23", "1"), ("C32", "1"), ("IC1", "3")],
    ideal="allow: each instance's C5:1 net joins vbias (requested; net-mates come along)",
    pin_nodes=[("C5", "1"), ("R4", "1"), ("C14", "1"), ("R15", "1")])
log.save()
