"""Design name to module: which implementation of the three routing tools a suite runs.

A design exposes wire_pins_to_net, connect_pins, auto_place_decoupling_cap and pin_positions (and
MODULE for the prototypes) at module level. hpaths.DESIGNS holds the names a run selected
(ROUTING_HARNESS_DESIGNS, default X); selected() returns them in that order.

  X    adapters/x_impl.py: the repository's own wire_pins_to_net at the checkout under test
  Hb2  protos/hb2_impl.py: the chosen prototype (h2_impl.py with CROSS_BAN = True)
  Hb   protos/hb_impl.py: H with CROSS_BAN = True
  H    protos/h_impl.py
  M2   protos/m2_impl.py: the first precise design
  E    protos/e_impl.py: writes only into empty space

K (coarse_impl.py, the coarse refusal model of follow-up 1) is not ported.
"""

import importlib

import hpaths

NAMES = {
    "X": "x_impl",
    "Hb2": "hb2_impl",
    "Hb": "hb_impl",
    "H": "h_impl",
    "M2": "m2_impl",
    "E": "e_impl",
}

_CACHE: dict = {}


def load(name):
    """The module of design `name`, imported once."""
    if name not in NAMES:
        raise ValueError(
            f"unknown design {name!r}; valid names: {', '.join(NAMES)} "
            "(design K, coarse_impl.py, is not ported)"
        )
    if name not in _CACHE:
        _CACHE[name] = importlib.import_module(NAMES[name])
    return _CACHE[name]


def selected():
    """{name: module} for hpaths.DESIGNS, in the order given."""
    return {name: load(name) for name in hpaths.DESIGNS}
