"""Design X: the repo's own tools at the checkout under test, as the harness runs a design.

wire_pins_to_net is the tool in the target's mcp_server_kicad.schematic (this checkout, or the
tree PT_REPO names; hpaths puts it first on sys.path). Importing this module raises when that tool
still takes auto_pwr_flag: such a target predates slice 1 of the routing fix, and X is meaningless
on it. The adapter only maps the tool's API onto the harness's:
- auto_pwr_flag is accepted and dropped: the tool has no such parameter (main removed the
  automatic PWR_FLAG in #62, before slice 1);
- reason codes [code] become [H:code], the prefix the harness regexes accept;
- a ToolError carrying no code (a refusal raised before planning, such as a missing file) becomes
  [H:validation], and every refusal is re-raised as a plain ToolError, the class name
  judge3.call counts as REFUSED (the tool itself raises a subclass of it).
connect_pins, auto_place_decoupling_cap, pin_positions and MODULE are Hb2's, because slices 2
and 3 have not been built (docs/adr-routing-safety.md, status log): the gate measures
wire_pins_to_net.
"""

import inspect
import re

import hpaths  # noqa: F401  (repo under test on sys.path)
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import schematic as _REPO

if "auto_pwr_flag" in inspect.signature(_REPO.wire_pins_to_net).parameters:
    raise RuntimeError(
        "design X needs a target whose wire_pins_to_net has no auto_pwr_flag parameter, but "
        f"{_REPO.__file__} still has one. That target predates slice 1 of the routing fix (main "
        "dropped the parameter in #62), so X is meaningless on it. Run a prototype design (Hb2, "
        "Hb, H, M2, E) there instead, through ROUTING_HARNESS_DESIGNS, or point PT_REPO at a "
        "tree that has slice 1."
    )

import hb2_impl as _HB2  # noqa: E402

_CODE = re.compile(r"\[([a-z0-9_]+)\]")
_LINE = re.compile(r"- ((?:\[H:\w+\]\s*)+)(\S+:\S+?)( \(pad [^)]*\))?: (.*)")
_AMBIGUOUS = "matches several pads that are not drawn at one point"


def _recode(text: str) -> str:
    """The branch's refusal in the harness's grammar (Hb2's), meaning unchanged:
    - [code] becomes [H:code];
    - each per-pin line '- [codes] TAG: reason' becomes '- REFUSED [H:codes] TAG: reason', the
      form the adaptive policies parse (layouts runner.refused_lines, llm-workflow, probeB); a
      stacked-name tag 'REF:NAME (pad N)' keeps the requested 'REF:NAME' and moves '(pad N)'
      into the reason;
    - an ambiguous pin name also carries Hb2's phrase 'matches several pads (a, b)', which the
      adaptive policies read to retry with pad numbers.
    """
    out = []
    for line in text.splitlines():
        line = _CODE.sub(lambda m: f"[H:{m.group(1)}]", line)
        m = _LINE.match(line)
        if m:
            codes, tag, pad, reason = m.groups()
            reason = (pad.strip() + " " if pad else "") + reason
            if _AMBIGUOUS in reason:
                nums = re.findall(r"pad (\S+?) (?:at|not drawn)", reason)
                reason += f" (matches several pads ({', '.join(nums)}))"
            line = f"- REFUSED {codes.strip()} {tag}: {reason}"
        out.append(line)
    text = "\n".join(out)
    return text if text.startswith("[H:") else "[H:validation] " + text


def wire_pins_to_net(
    pins, label_text, direction="auto", stub_length=2.54, auto_pwr_flag=True, schematic_path=None
):
    try:
        return _REPO.wire_pins_to_net(
            pins,
            label_text,
            direction=direction,
            stub_length=stub_length,
            schematic_path=schematic_path,
        )
    except ToolError as e:
        raise ToolError(_recode(str(e))) from None


connect_pins = _HB2.connect_pins
auto_place_decoupling_cap = _HB2.auto_place_decoupling_cap
pin_positions = _HB2.pin_positions
MODULE = _HB2.MODULE
