"""Design Hb2: h2_impl.py (H with the follow-up 3 H-C1 pad-map rule) loaded as a separate module object with CROSS_BAN = True (H-C19).

The source is h2_impl.py byte for byte; only the module constant differs, set before any call.
"""

import importlib.util as _ilu
from pathlib import Path as _P

_spec = _ilu.spec_from_file_location("h2_impl_crossban", _P(__file__).with_name("h2_impl.py"))
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_mod.CROSS_BAN = True

wire_pins_to_net = _mod.wire_pins_to_net
connect_pins = _mod.connect_pins
auto_place_decoupling_cap = _mod.auto_place_decoupling_cap
pin_positions = _mod.pin_positions
MODULE = _mod
