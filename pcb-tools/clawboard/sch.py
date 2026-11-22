"""In-memory model of a flat (single sheet) ``.kicad_sch`` file.

Connectivity is resolved geometrically (wires, junctions, labels, power symbols,
pins) so the netlist matches what KiCad would export for a non-hierarchical sheet.
"""
from __future__ import annotations

import math
from pathlib import Path

from . import library
from .geom import pt_seg_dist
from .sexpr import S, dumps, find, find_all, new_uuid, num, parse, val

STUB = 2.54


def _key(x, y) -> tuple[int, int]:
    return int(round(x * 100)), int(round(y * 100))


def _xform(px, py, rot_, mirror) -> tuple[float, float]:
    """Library (Y-up) point -> schematic offset (Y-down) for a placed symbol."""
    if mirror == "x":
        py = -py
    elif mirror == "y":
        px = -px
    r = int(round(rot_)) % 360
    if r == 0:
        return px, -py
    if r == 90:
        return -py, -px
    if r == 180:
        return -px, py
    return py, px


class SchPin:
    __slots__ = ("ref", "number", "name", "etype", "x", "y", "dx", "dy")


class SchSymbol:
    __slots__ = ("ref", "value", "lib_id", "footprint", "x", "y", "rot", "mirror", "pins", "node", "uuid", "virtual")

    def pin(self, number) -> "SchPin | None":
        for p in self.pins:
            if p.number == str(number):
                return p
        return None


class Schematic:
    def __init__(self, path):
        self.path = Path(path)
        self.tree = parse(self.path.read_text(encoding="utf-8"))
        if not self.tree or self.tree[0] != "kicad_sch":
            raise ValueError(f"{path} is not a KiCad schematic file")
        self.index()

    # ------------------------------------------------------------------ indexing
    def index(self) -> None:
        t = self.tree
        self.uuid = str(val(t, "uuid", ""))
        self.lib_pins: dict[str, list[dict]] = {}
        self.lib_power: set[str] = set()
        libs = find(t, "lib_symbols") or []
        for ls in find_all(libs, "symbol"):
            name = str(ls[1])
            pins: list[dict] = []
            self._collect_pins(ls, pins)
            self.lib_pins[name] = pins
            if find(ls, "power") is not None:
                self.lib_power.add(name)
        self.symbols: list[SchSymbol] = []
        self.wires: list[tuple[tuple[float, float], tuple[float, float], list]] = []
        self.labels: list[dict] = []
        self.junctions: list[tuple[float, float]] = []
        self.no_connects: dict[tuple[int, int], list] = {}
        for node in t:
            if not isinstance(node, list) or not node:
                continue
            h = node[0]
            if h == "symbol" and find(node, "lib_id") is not None:
                self.symbols.append(self._read_symbol(node))
            elif h == "wire":
                pts = find_all(find(node, "pts"), "xy")
                if len(pts) >= 2:
                    self.wires.append(((num(pts[0][1]), num(pts[0][2])), (num(pts[-1][1]), num(pts[-1][2])), node))
            elif h in ("label", "global_label", "hierarchical_label"):
                at = find(node, "at")
                self.labels.append({"name": str(node[1]), "x": num(at[1]), "y": num(at[2]), "kind": str(h), "node": node})
            elif h == "junction":
                at = find(node, "at")
                self.junctions.append((num(at[1]), num(at[2])))
            elif h == "no_connect":
                at = find(node, "at")
                self.no_connects[_key(num(at[1]), num(at[2]))] = node
        self.by_ref = {s.ref: s for s in self.symbols}
        self._netlist = None

    def _collect_pins(self, node, out: list) -> None:
        for c in node:
            if not isinstance(c, list) or not c:
                continue
            if c[0] == "pin" and len(c) > 3:
                at = find(c, "at")
                nm, nu = find(c, "name"), find(c, "number")
                out.append({
                    "type": str(c[1]), "x": num(at[1]), "y": num(at[2]), "angle": num(at[3]) if len(at) > 3 else 0.0,
                    "length": num(val(c, "length", 2.54)), "name": str(nm[1]) if nm else "", "number": str(nu[1]) if nu else "",
                })
            elif c[0] == "symbol":
                self._collect_pins(c, out)

    def _read_symbol(self, node) -> SchSymbol:
        s = SchSymbol()
        s.node, s.uuid = node, str(val(node, "uuid", ""))
        s.lib_id = str(val(node, "lib_id", ""))
        at = find(node, "at")
        s.x, s.y = num(at[1]), num(at[2])
        s.rot = num(at[3]) if len(at) > 3 else 0.0
        m = find(node, "mirror")
        s.mirror = str(m[1]) if m else ""
        s.ref, s.value, s.footprint = "?", "", ""
        for p in find_all(node, "property"):
            if p[1] == "Reference":
                s.ref = str(p[2])
            elif p[1] == "Value":
                s.value = str(p[2])
            elif p[1] == "Footprint":
                s.footprint = str(p[2])
        s.virtual = s.ref.startswith("#")
        s.pins = []
        for lp in self.lib_pins.get(s.lib_id, []):
            p = SchPin()
            p.ref, p.number, p.name, p.etype = s.ref, lp["number"], lp["name"], lp["type"]
            ox, oy = _xform(lp["x"], lp["y"], s.rot, s.mirror)
            p.x, p.y = round(s.x + ox, 4), round(s.y + oy, 4)
            a = math.radians(lp["angle"])
            # unit vector pointing away from the symbol body, in schematic space
            p.dx, p.dy = _xform(-math.cos(a), -math.sin(a), s.rot, s.mirror)
            p.dx, p.dy = round(p.dx), round(p.dy)
            s.pins.append(p)
        return s

    # ------------------------------------------------------------------ netlist
    def netlist(self) -> dict:
        """{'nets': {name: [(ref, pin, pin_name)]}, 'pin_net': {(ref, pin): name}, 'nc': {(ref, pin)}, 'floating': [...]}"""
        if self._netlist is not None:
            return self._netlist
        parent: dict = {}

        def find_(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find_(a), find_(b)
            if ra != rb:
                parent[rb] = ra

        points: list[tuple[float, float]] = []
        for a, b, _ in self.wires:
            union(_key(*a), _key(*b))
            points += [a, b]
        for lab in self.labels:
            points.append((lab["x"], lab["y"]))
        points += self.junctions
        pins = [p for s in self.symbols for p in s.pins]
        for p in pins:
            points.append((p.x, p.y))
            find_(_key(p.x, p.y))
        # anything that lands on a wire body joins that wire
        for (px, py) in set(points):
            for a, b, _ in self.wires:
                if min(a[0], b[0]) - 0.01 <= px <= max(a[0], b[0]) + 0.01 and min(a[1], b[1]) - 0.01 <= py <= max(a[1], b[1]) + 0.01:
                    if pt_seg_dist(px, py, *a, *b) < 0.011:
                        union(_key(px, py), _key(*a))
        names: dict[str, tuple[int, int]] = {}
        for lab in self.labels:
            k = _key(lab["x"], lab["y"])
            find_(k)
            if lab["name"] in names:
                union(names[lab["name"]], k)
            else:
                names[lab["name"]] = k
        for s in self.symbols:  # power symbols name their net after the Value
            if s.lib_id in self.lib_power:
                for p in s.pins:
                    k = _key(p.x, p.y)
                    if s.value in names:
                        union(names[s.value], k)
                    else:
                        names[s.value] = k
        root_name: dict = {}
        for nm in sorted(names):
            root_name.setdefault(find_(names[nm]), nm)
        nets: dict[str, list] = {}
        pin_net: dict[tuple[str, str], str] = {}
        nc: set[tuple[str, str]] = set()
        floating: list[tuple[str, str, str]] = []
        members: dict = {}
        for p in pins:
            if p.ref.startswith("#"):
                continue
            members.setdefault(find_(_key(p.x, p.y)), []).append(p)
        for root, plist in members.items():
            name = root_name.get(root)
            for p in plist:
                if _key(p.x, p.y) in self.no_connects:
                    nc.add((p.ref, p.number))
            if name is None:
                if len(plist) == 1:
                    p = plist[0]
                    if (p.ref, p.number) not in nc:
                        floating.append((p.ref, p.number, p.name))
                    continue
                first = sorted(plist, key=lambda q: (q.ref, q.number))[0]
                name = f"Net-({first.ref}-Pad{first.number})"
            for p in plist:
                nets.setdefault(name, []).append((p.ref, p.number, p.name))
                pin_net[(p.ref, p.number)] = name
        self._netlist = {"nets": nets, "pin_net": pin_net, "nc": nc, "floating": floating}
        return self._netlist

    # ------------------------------------------------------------------ mutation
    def _append(self, node) -> None:
        idx = next((i for i, c in enumerate(self.tree) if isinstance(c, list) and c and c[0] == "sheet_instances"), len(self.tree))
        self.tree.insert(idx, node)

    def ensure_lib_symbol(self, sym_name: str) -> str:
        full = f"{library.LIB}:{sym_name}"
        libs = find(self.tree, "lib_symbols")
        if libs is None:
            libs = [S("lib_symbols")]
            self.tree.insert(next((i for i, c in enumerate(self.tree) if isinstance(c, list) and c and c[0] in ("symbol", "wire", "label")), len(self.tree)), libs)
        if not any(isinstance(c, list) and len(c) > 1 and c[1] == full for c in libs):
            node = library.lib_symbol_node(sym_name)
            libs.append(node)
            pins: list[dict] = []
            self._collect_pins(node, pins)
            self.lib_pins[full] = pins
        return full

    def _label_node(self, name, x, y, dx, dy) -> list:
        if dx < 0:
            ang, just = 180, ("right", "bottom")
        elif dx > 0:
            ang, just = 0, ("left", "bottom")
        elif dy < 0:
            ang, just = 90, ("left", "bottom")
        else:
            ang, just = 270, ("right", "bottom")
        return [
            S("label"), name, [S("at"), round(x, 4), round(y, 4), ang],
            [S("effects"), [S("font"), [S("size"), 1.27, 1.27]], [S("justify")] + [S(j) for j in just]],
            [S("uuid"), new_uuid()],
        ]

    def _wire_node(self, x1, y1, x2, y2) -> list:
        return [
            S("wire"), [S("pts"), [S("xy"), round(x1, 4), round(y1, 4)], [S("xy"), round(x2, 4), round(y2, 4)]],
            [S("stroke"), [S("width"), 0], [S("type"), S("default")]], [S("uuid"), new_uuid()],
        ]

    def add_symbol(self, sym_name, ref, value, footprint, x, y, pin_nets=None, project="project") -> SchSymbol:
        """Place a library symbol; every pin gets a short wire stub + net label (or a no-connect flag)."""
        pin_nets = {str(k): v for k, v in (pin_nets or {}).items()}
        full = self.ensure_lib_symbol(sym_name)
        spec = library.SYMBOLS[sym_name]
        virtual = bool(spec.get("virtual"))
        uid = new_uuid()

        def prop(name, text, px, py, hide=False, just=None):
            eff = [S("effects"), [S("font"), [S("size"), 1.27, 1.27]]]
            if just:
                eff.append([S("justify"), S(just)])
            if hide:
                eff.append([S("hide"), S("yes")])
            return [S("property"), name, text, [S("at"), round(px, 4), round(py, 4), 0], eff]

        rx, ry = spec["ref_at"]
        vx, vy = spec["val_at"]
        node = [
            S("symbol"), [S("lib_id"), full], [S("at"), x, y, 0], [S("unit"), 1],
            [S("exclude_from_sim"), S("no")], [S("in_bom"), S("no" if virtual else "yes")], [S("on_board"), S("no" if virtual else "yes")],
            [S("dnp"), S("no")], [S("uuid"), uid],
            prop("Reference", ref, x + rx, y - ry, hide=virtual, just="left"),
            prop("Value", value, x + vx, y - vy, just="left"),
            prop("Footprint", footprint or "", x, y, hide=True),
            prop("Datasheet", "~", x, y, hide=True),
            prop("Description", spec["desc"], x, y, hide=True),
        ]
        for p in spec["pins"]:
            node.append([S("pin"), p[6][1], [S("uuid"), new_uuid()]])
        node.append([S("instances"), [S("project"), project, [S("path"), f"/{self.uuid}", [S("reference"), ref], [S("unit"), 1]]]])
        self._append(node)
        sym = self._read_symbol(node)
        for p in sym.pins:
            net = pin_nets.get(p.number)
            if virtual:
                if net:
                    self._append(self._label_node(net, p.x, p.y, 0, 1))
                continue
            if net:
                ex, ey = p.x + p.dx * STUB, p.y + p.dy * STUB
                self._append(self._wire_node(p.x, p.y, ex, ey))
                self._append(self._label_node(net, ex, ey, p.dx, p.dy))
            else:
                self._append([S("no_connect"), [S("at"), p.x, p.y], [S("uuid"), new_uuid()]])
        self.index()
        return self.by_ref[ref]

    def set_pin_net(self, ref: str, pin: str, net: str) -> str:
        """Re-label one pin.  Returns the previous net name."""
        sym = self.by_ref.get(ref)
        p = sym.pin(pin) if sym else None
        if p is None:
            raise KeyError(f"schematic has no pin {ref}.{pin}")
        old = self.netlist()["pin_net"].get((ref, str(pin)), "")
        pk = _key(p.x, p.y)
        if pk in self.no_connects:
            node = self.no_connects[pk]
            self.tree[:] = [c for c in self.tree if c is not node]
        end = None
        for a, b, _ in self.wires:
            if _key(*a) == pk:
                end = b
            elif _key(*b) == pk:
                end = a
        target = _key(*end) if end else pk
        for lab in self.labels:
            if _key(lab["x"], lab["y"]) in (target, pk):
                lab["node"][1] = net
                self.index()
                return old
        if end is not None:
            raise ValueError(f"{ref}.{pin} is wired to other parts; re-label it manually")
        ex, ey = p.x + p.dx * STUB, p.y + p.dy * STUB
        self._append(self._wire_node(p.x, p.y, ex, ey))
        self._append(self._label_node(net, ex, ey, p.dx, p.dy))
        self.index()
        return old

    def rename_net(self, old: str, new: str) -> int:
        n = 0
        for lab in self.labels:
            if lab["name"] == old:
                lab["node"][1] = new
                n += 1
        self.index()
        return n

    def next_ref(self, prefix: str, taken: "set[str] | None" = None) -> str:
        used = {s.ref for s in self.symbols} | (taken or set())
        i = 1
        while f"{prefix}{i}" in used:
            i += 1
        return f"{prefix}{i}"

    def free_slot(self, origin=(292.1, 213.36), pitch=12.7, per_row=8, row_pitch=38.1) -> tuple[float, float]:
        """Next empty cell of the 'agent patches' area of the sheet."""
        taken = {_key(s.x, s.y) for s in self.symbols}
        for i in range(64):
            x = origin[0] + (i % per_row) * pitch
            y = origin[1] + (i // per_row) * row_pitch
            if _key(x, y) not in taken:
                return round(x, 4), round(y, 4)
        return origin

    def save(self, path=None) -> None:
        Path(path or self.path).write_text(dumps(self.tree), encoding="utf-8", newline="\n")
