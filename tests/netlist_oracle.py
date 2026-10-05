"""KiCad's own netlist as the judge of a schematic edit, net by net and node by node.

A tool that edits wiring is right only if KiCad reads the result as the requested join and
nothing else. So the oracle runs ``kicad-cli sch export netlist`` before and after and compares
the two by node set, never by name: kicadxml prints net names unescaped, so two different nets
can print the same name, and a rename keeps the node set while changing the name.

``judge`` reports every way an edit can go wrong (the verdict kinds of the routing pressure
test's judge, docs/adr-routing-safety.md):

- ``bad_merges``: an after-net joins before-nets outside the requested join;
- ``splits``: a before-net's nodes end up in two or more nets;
- ``renames``: a net keeps its nodes but changes its name, outside the requested join or,
  for a net that carried a user name, inside it;
- ``class_changes``: a node's net class changes, other than a requested node taking a class one
  of the joined nets already had;
- ``pads_multi``: a node is listed in more nets than before (a pad in two nets);
- ``vanished`` / ``appeared``: a node disappears or appears;
- ``named_merge``: one after-net holds two before-nets that each carried a user name, even
  inside the requested join, since putting a pin on N never means joining +5V to GND;
- ``req_pad_multi``: a requested node sits in two or more nets after a write;
- ``delivered``: every requested node is in exactly one net, the same one, and it is N.

Only the kicad-cli reading. The routing design also judged an emulation of the KiCad 9 GUI's
load-time cleanup, which merges collinear overlaps; this repo checks overlaps structurally
instead (routing_checks.lint_new_geometry).

``model_disagreements`` turns the same netlist on the routing model itself: the narrow view must
join nothing kicad-cli keeps apart, and the possible view must hold every join kicad-cli makes.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mcp_server_kicad._shared import _run_cli

Node = tuple[str, str]
#: (code, printed name, net class, nodes)
Net = tuple[str, str, str, frozenset[Node]]

# KiCad's own names for unnamed nets. Tested on the raw name: stripping a sheet path first cuts
# "unconnected-(U1-PB7/PB8-Pad1)" to "PB8-Pad1)", which would then read as a user name.
_AUTO_NAME = re.compile(r"(^|/)(Net|unconnected)-\(")


def parse_kicadxml(data: bytes | str) -> list[Net]:
    """The nets of a kicadxml netlist, in file order."""
    root = ET.fromstring(data)
    return [
        (
            net.get("code", ""),
            net.get("name", ""),
            net.get("class", "") or "",
            frozenset((n.get("ref", ""), n.get("pin", "")) for n in net.findall("node")),
        )
        for net in root.iter("net")
    ]


def nets(path: str | Path) -> list[Net]:
    """KiCad's netlist of the schematic at *path* (judge the root of a hierarchy)."""
    path = Path(path)
    out = path.with_name(path.name + ".oracle.xml")
    if out.exists():
        out.unlink()
    result = _run_cli(
        ["sch", "export", "netlist", "--format", "kicadxml", "--output", str(out), str(path)],
        check=False,
    )
    if not out.exists():
        raise RuntimeError(
            f"kicad-cli netlist export failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    try:
        return parse_kicadxml(out.read_bytes())
    finally:
        os.remove(out)


def bare_name(name: str) -> str:
    """A printed net name without its sheet path: "/sub/VCC" -> "VCC"."""
    return name.rsplit("/", 1)[-1] if "/" in name else name


def is_named(name: str, n: str) -> bool:
    """A printed net name is N, with or without a sheet path. kicadxml prints a name's "/"
    unescaped ("A{slash}B" as /A/B), so the path cannot be split off at the last "/"."""
    return name == n or name.endswith("/" + n)


def _user_name(name: str) -> bool:
    return bool(name) and not _AUTO_NAME.search(name)


@dataclass
class Verdict:
    bad_merges: list = field(default_factory=list)
    splits: list = field(default_factory=list)
    renames: list = field(default_factory=list)
    class_changes: list = field(default_factory=list)
    pads_multi: list = field(default_factory=list)
    vanished: list = field(default_factory=list)
    appeared: list = field(default_factory=list)
    named_merge: list = field(default_factory=list)
    req_pad_multi: list = field(default_factory=list)
    delivered: bool = False

    _KINDS = (
        "bad_merges",
        "splits",
        "renames",
        "class_changes",
        "pads_multi",
        "vanished",
        "appeared",
        "named_merge",
        "req_pad_multi",
    )

    @property
    def wrong(self) -> bool:
        return any(getattr(self, k) for k in self._KINDS)

    def problems(self) -> dict:
        return {k: getattr(self, k) for k in self._KINDS if getattr(self, k)}


def _where(netlist: list[Net]) -> dict[Node, list[int]]:
    out: dict[Node, list[int]] = {}
    for i, (_c, _n, _k, nodes) in enumerate(netlist):
        for node in nodes:
            out.setdefault(node, []).append(i)
    return out


def judge(
    before: list[Net],
    after: list[Net],
    groups: Iterable[Iterable[Node]],
    n: str | None = None,
    *,
    wrote: bool = True,
) -> Verdict:
    """Compare two netlists of one schematic around one edit.

    *groups* are the requested joins: for wire_pins_to_net one group holding every requested
    pad, with *n* the net name; for connect_pins one group of the two pins and no *n*. The
    allowed join is the before-nets of the requested nodes plus every before-net named *n*.
    *wrote* says the call changed the file, which is when a requested pad left in two nets
    counts against it.
    """
    groups = [frozenset(g) for g in groups]
    bw, aw = _where(before), _where(after)
    bnet = {node: ix[0] for node, ix in bw.items()}
    anet = {node: ix[0] for node, ix in aw.items()}
    allowed = set()
    for g in groups:
        allowed |= {bnet[node] for node in g if node in bnet}
    if n is not None:
        allowed |= {i for i, (_c, name, _k, _v) in enumerate(before) if is_named(name, n)}
    v = Verdict()

    for _c, name, _k, nodes in after:
        src = {bnet[node] for node in nodes if node in bnet}
        if len(src) > 1 and not src <= allowed:
            v.bad_merges.append((name, sorted(before[i][1] for i in src)))
        users = [before[i][1] for i in sorted(src) if _user_name(before[i][1])]
        if len(src) > 1 and len(users) > 1:
            v.named_merge.append((name, sorted(users)))

    for _c, name, _k, nodes in before:
        dst = {anet[node] for node in nodes if node in anet}
        if len(dst) > 1:
            v.splits.append((name, sorted(after[j][1] for j in dst)))

    # A node set listed twice (pad copies on two nets) has no single identity to rename.
    bcount = Counter(nodes for *_x, nodes in before if nodes)
    acount = Counter(nodes for *_x, nodes in after if nodes)
    bsets = {nodes: (i, name) for i, (_c, name, _k, nodes) in enumerate(before) if nodes}
    for _c, name, _k, nodes in after:
        if not nodes or acount[nodes] != 1 or bcount[nodes] != 1 or nodes not in bsets:
            continue
        i, old = bsets[nodes]
        # Inside the join an unnamed net may take N; a user-named one keeps its name.
        if old != name and (i not in allowed or _user_name(old)):
            v.renames.append((old, name))

    joined_classes = {before[i][2] for i in allowed}
    for node, j in anet.items():
        if node not in bnet:
            continue
        i = bnet[node]
        old, new = before[i][2], after[j][2]
        if old != new and not (i in allowed and new in joined_classes):
            v.class_changes.append((node, old, new))

    v.pads_multi = sorted(node for node, ix in aw.items() if len(ix) > len(bw.get(node, [0])))
    v.vanished = sorted(set(bnet) - set(anet))
    v.appeared = sorted(set(anet) - set(bnet))

    requested = set().union(*groups) if groups else set()
    if wrote:
        v.req_pad_multi = sorted(node for node in requested if len(aw.get(node, [])) > 1)

    delivered = bool(groups)
    for g in groups:
        homes = {j for node in g for j in aw.get(node, [])}
        if len(homes) != 1 or any(len(aw.get(node, [])) != 1 for node in g):
            delivered = False
            continue
        if n is not None:
            (j,) = homes
            was_n = any(is_named(before[bnet[node]][1], n) for node in after[j][3] if node in bnet)
            delivered &= is_named(after[j][1], n) or was_n
    v.delivered = delivered
    return v


def model_disagreements(path: str | Path, netlist: list[Net] | None = None):
    """Where the routing model's views disagree with kicad-cli's netlist of the same file.

    Returns (splits, misses), or None when the model refuses the whole sheet ([unloadable],
    [derived]):

    - splits: narrow components whose pins kicad-cli does not list on one common net, so the
      narrow view joined pins a reader keeps apart and a no-op built on it could be false;
    - misses: kicad-cli nets whose pins fall in two or more possible components, at least one
      of which reaches nothing the tool refuses anyway, so a refusal built on the possible view
      could be missing.

    Pins are matched to netlist nodes by reference and pad number. Power symbols' pins (a
    reference starting with "#") are not netlist nodes, and a pin with no node is skipped.
    """
    from mcp_server_kicad import _connectivity, _cst  # the judge above needs neither

    root = _cst.parse(Path(path).read_bytes()).lists[0]
    try:
        m = _connectivity.Model(root)
        m.check_loadable()
    except _connectivity.Refusal:
        return None
    if netlist is None:
        netlist = nets(path)
    where: dict[Node, set[int]] = {}
    for i, (_c, _n, _k, nodes) in enumerate(netlist):
        for node in nodes:
            where.setdefault(node, set()).add(i)
    pins: dict[Node, list] = {}
    for it in m.items:
        if it.kind == "pin" and it.ref and not it.ref.startswith("#"):
            pins.setdefault((it.ref, it.num or ""), []).append(it)
    m.ensure()
    narrow: dict[int, set[Node]] = {}
    for node, its in pins.items():
        if node in where:
            for it in its:
                narrow.setdefault(m._C.find(it.id), set()).add(node)
    splits = [
        sorted(nodes)
        for nodes in narrow.values()
        if len(nodes) > 1 and not set.intersection(*(where[n] for n in nodes))
    ]
    cm = m.coarse()
    misses = []
    for _c, name, _k, nodes in netlist:
        comps: dict[int, set[Node]] = {}
        for node in nodes:
            for it in pins.get(node, ()):
                comps.setdefault(cm.uf.find(it.id), set()).add(node)
        if len(comps) > 1 and any(not cm.bad.get(r) for r in comps):
            misses.append((name, [sorted(v) for v in comps.values()]))
    return splits, misses
