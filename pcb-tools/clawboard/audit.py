"""DFM + ERC audit: real electrical and manufacturing rules, compact JSON output."""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from .geom import shape_dist
from .pcb import Board, clearance_violations, edge_violations, ratsnest
from .pour import pour_report
from .project import Project

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

_SI = {"p": 1e-3, "n": 1.0, "u": 1e3, "µ": 1e3, "μ": 1e3, "m": 1e6}


def parse_cap_nf(text: str) -> "float | None":
    """'100nF', '0.1uF', '4u7', '22 pF' -> nanofarads."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([pnuµμm])\s*(\d*)\s*F?\b", text or "", re.I)
    if not m:
        return None
    whole, unit, frac = m.group(1), m.group(2).lower(), m.group(3)
    v = float(f"{whole}.{frac}") if frac and "." not in whole else float(whole)
    return v * _SI.get(unit, 1.0)


def parse_res_ohm(text: str) -> "float | None":
    """'5.1k', '4k7', '10K', '100R', '1M', '330' -> ohms."""
    t = (text or "").strip().replace("Ω", "").replace("ohm", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([kKmMrR]?)\s*(\d*)$", t)
    if not m:
        return None
    whole, unit, frac = m.groups()
    v = float(f"{whole}.{frac}") if frac and "." not in whole else float(whole)
    mult = {"k": 1e3, "K": 1e3, "M": 1e6, "m": 1e6, "r": 1.0, "R": 1.0, "": 1.0}[unit]
    return v * mult


def ipc2221_amps(width_mm: float, oz: float = 1.0, rise_c: float = 10.0) -> float:
    """External-layer ampacity per IPC-2221."""
    area_mil2 = (width_mm / 0.0254) * (1.378 * oz)
    return 0.048 * (rise_c ** 0.44) * (area_mil2 ** 0.725)


def load_rules(fab_house: str = "jlcpcb", layers: int = 2) -> dict:
    path = RULES_DIR / f"{fab_house.lower()}.json"
    if not path.exists():
        raise ValueError(f"unknown fab house '{fab_house}' (available: {', '.join(p.stem for p in RULES_DIR.glob('*.json') if p.stem != 'parts')})")
    data = json.loads(path.read_text(encoding="utf-8"))
    prof = data["profiles"]["4layer" if layers >= 4 else "2layer"]
    parts = json.loads((RULES_DIR / "parts.json").read_text(encoding="utf-8"))
    return {"fab": data["fab"], "profile": prof, "power": data["power"], "placement": data["placement"], "erc": data["erc"], "parts": parts}


class _Issues:
    def __init__(self):
        self.items: list[dict] = []
        self._seen: set[str] = set()

    def add(self, id_, rule, severity, title, detail="", refs=(), nets=(), at=None, layer=None, hint=""):
        base, k = id_, 2
        while id_ in self._seen:
            id_ = f"{base}#{k}"
            k += 1
        self._seen.add(id_)
        it = {"id": id_, "rule": rule, "severity": severity, "title": title}
        if detail:
            it["detail"] = detail
        if refs:
            it["refs"] = list(refs)
        if nets:
            it["nets"] = list(nets)
        if at:
            it["at"] = [round(at[0], 2), round(at[1], 2)]
        if layer:
            it["layer"] = layer
        if hint:
            it["fix"] = hint
        self.items.append(it)


def _component_view(proj: Project) -> dict:
    """ref -> {value, lib, pins: {num: {name, net}}} from the schematic (PCB as fallback)."""
    comps: dict[str, dict] = {}
    if proj.sch is not None:
        nl = proj.sch.netlist()
        for s in proj.sch.symbols:
            if s.virtual:
                continue
            comps[s.ref] = {
                "value": s.value, "lib": s.lib_id,
                "pins": {p.number: {"name": p.name, "net": nl["pin_net"].get((s.ref, p.number), "")} for p in s.pins},
            }
    if proj.board is not None:
        for f in proj.board.footprints:
            c = comps.setdefault(f.ref, {"value": f.value, "lib": f.lib, "pins": {}})
            for p in f.pads:
                if p.num and p.num not in c["pins"]:
                    from .sexpr import val
                    c["pins"][p.num] = {"name": str(val(p.node, "pinfunction", "")), "net": p.net}
    return comps


def run_audit(project_dir, fab_house: str = "jlcpcb", layers: "int | None" = None, quick: bool = False, pour: "dict | None" = None) -> dict:
    t0 = time.perf_counter()
    proj = project_dir if isinstance(project_dir, Project) else Project(project_dir)
    board: "Board | None" = proj.board
    rules = load_rules(fab_house, layers or 2)
    prof, pw, erc = rules["profile"], rules["power"], rules["erc"]
    gnd_re = re.compile(erc["ground_regex"], re.I)
    power_re = re.compile(pw["power_net_regex"], re.I)
    hi_re = re.compile(pw["high_current_regex"], re.I)
    iss = _Issues()
    comps = _component_view(proj)
    checked: list[str] = []

    net_pins: dict[str, list[tuple[str, str, str]]] = {}
    for ref, c in comps.items():
        for pin_num, p in c["pins"].items():
            if p["net"]:
                net_pins.setdefault(p["net"], []).append((ref, pin_num, p["name"]))

    def is_gnd(net):
        return bool(net and gnd_re.search(net))

    def is_power(net):
        return bool(net and not is_gnd(net) and power_re.search(net))

    def pad_xy(ref, pin):
        if board is None:
            return None
        f = board.fp_by_ref.get(ref)
        p = f.pad(pin) if f else None
        return (p.x, p.y) if p else ((f.x, f.y) if f else None)

    def two_pin_parts(prefix, net):
        """Two-terminal parts with one pin on ``net`` -> [(ref, value, other_net, pin_on_net)]."""
        out = []
        for ref, pin_num, _ in net_pins.get(net, []):
            c = comps[ref]
            if not ref.startswith(prefix) or len(c["pins"]) != 2:
                continue
            other = next((q["net"] for k, q in c["pins"].items() if k != pin_num), "")
            out.append((ref, c["value"], other, pin_num))
        return out

    # ---------------------------------------------------------------- ERC: USB-C CC pull-downs
    checked.append("usb_c_cc_pulldown")
    lo, hi = erc["usb_cc_rd_ohm"]
    for ref, c in comps.items():
        cc = {p["name"].upper(): (k, p["net"]) for k, p in c["pins"].items() if re.fullmatch(r"CC[12]", p["name"].upper())}
        if len(cc) < 2:
            continue
        (p1, n1), (p2, n2) = cc["CC1"], cc["CC2"]
        if n1 and n1 == n2:
            iss.add(
                f"USB-CC-{ref}", "usb_c_cc_pulldown", "error",
                f"{ref}: CC1 and CC2 are tied together on net {n1}",
                "USB Type-C sinks need a separate 5.1k Rd from each CC pin to GND. With the pins shorted, e-marked cables present Ra||Rd and the source never enables VBUS.",
                refs=[ref], nets=[n1], at=pad_xy(ref, p2),
                hint=f"set_pin_net {ref}.{p2} -> USB_CC2, then add_component resistor 5.1k (USB_CC2 to GND) near {ref}.{p2}",
            )
            continue
        for label, (pin, net) in sorted(cc.items()):
            ok = any(is_gnd(o) and (parse_res_ohm(v) or 0) >= lo and (parse_res_ohm(v) or 0) <= hi for _, v, o, _ in two_pin_parts("R", net)) if net else False
            if not ok:
                iss.add(
                    f"USB-CC-{ref}-{label}", "usb_c_cc_pulldown", "error",
                    f"{ref}.{pin} ({label}) has no 5.1k pull-down to GND",
                    "Each CC pin of a USB-C sink needs its own 5.1k (+/-10%) Rd to GND.",
                    refs=[ref], nets=[net] if net else [], at=pad_xy(ref, pin),
                    hint=f"add_component resistor 5.1k ({net or 'new net'} to GND) near {ref}.{pin}",
                )

    # ---------------------------------------------------------------- ERC: decoupling capacitors
    checked.append("decoupling")
    part_rules = rules["parts"]["parts"]
    generic = rules["parts"]["generic_ic"]
    gen_re = re.compile(generic["supply_pin_regex"], re.I)
    missing: dict[tuple, dict] = {}
    too_far: dict[tuple, dict] = {}
    reqs = []  # (ref, pin_num, pin, requirement, [(dist, cap_ref, cap_value)])
    for ref, c in sorted(comps.items()):
        if not re.match(r"^(U|IC)\d", ref):
            continue
        rule = next((r for r in part_rules if re.search(r["match"], f"{c['value']} {c['lib']}", re.I)), None)
        wanted = []
        if rule:
            for d in rule["decoupling"]:
                for k, p in c["pins"].items():
                    if p["name"].upper() == d["pin"].upper():
                        wanted.append((k, p, d))
        else:
            for k, p in c["pins"].items():
                if gen_re.match(p["name"]):
                    wanted.append((k, p, generic["decoupling"]))
        for pin_num, p, d in wanted:
            net = p["net"]
            if not net or is_gnd(net):
                continue
            caps = []
            for cref, cval, other, cpin in two_pin_parts("C", net):
                nf = parse_cap_nf(cval)
                if nf is None or not is_gnd(other) or not (d["min_nf"] <= nf <= d["max_nf"]):
                    continue
                a, b = pad_xy(ref, pin_num), pad_xy(cref, cpin)
                dist = math.hypot(a[0] - b[0], a[1] - b[1]) if a and b else 0.0
                caps.append((dist, cref, cval))
            reqs.append((ref, pin_num, p, d, caps))
    # every capacitor "belongs" to the IC it sits closest to; a far-away cap that belongs to
    # another IC does not count as this IC's decoupler (it is simply missing here)
    owner: dict[str, tuple[float, str]] = {}
    for ref, _, _, _, caps in reqs:
        for dist, cref, _ in caps:
            if cref not in owner or dist < owner[cref][0]:
                owner[cref] = (dist, ref)
    for ref, pin_num, p, d, caps in reqs:
        net = p["net"]
        if any(dist <= d["max_dist_mm"] for dist, _, _ in caps) or (caps and board is None):
            continue
        mine = [c_ for c_ in caps if owner[c_[1]][1] == ref]
        if not mine:
            m = missing.setdefault((ref, net, d["label"]), {"pins": [], "pin_num": pin_num, "d": d, "net": net})
            m["pins"].append(f"{p['name']}({pin_num})")
            continue
        dist, cref, cval = min(mine)
        m = too_far.setdefault((ref, cref), {"pins": [], "pin_num": pin_num, "dist": dist, "d": d, "net": net, "cval": cval})
        m["pins"].append(f"{p['name']}({pin_num})")
        if dist <= m["dist"]:
            m["dist"], m["pin_num"] = dist, pin_num
    for (ref, net, label), m in missing.items():
        iss.add(
            f"DECAP-{ref}-{label.split()[0]}", "decoupling_missing", "error",
            f"{ref} ({comps[ref]['value']}): no {label} decoupling capacitor on {net}",
            f"Supply pin {', '.join(m['pins'])} needs a {label} capacitor to GND within {m['d']['max_dist_mm']} mm.",
            refs=[ref], nets=[net], at=pad_xy(ref, m["pin_num"]),
            hint=f"add_component capacitor {label.split()[0]} ({net} to GND) near {ref}.{m['pin_num']}",
        )
    for (ref, cref), m in too_far.items():
        iss.add(
            f"DECAP-FAR-{cref}", "decoupling_too_far", "error",
            f"{cref} ({m['cval']}) is {m['dist']:.1f} mm from {ref} {', '.join(m['pins'])} (max {m['d']['max_dist_mm']} mm)",
            "A decoupling capacitor only works when its loop inductance is small: place it right next to the supply pin.",
            refs=[cref, ref], nets=[m["net"]], at=pad_xy(cref, "1"),
            hint=f"move_footprint {cref} near {ref}.{m['pin_num']}",
        )

    # ---------------------------------------------------------------- ERC: I2C pull-ups
    checked.append("i2c_pullup")
    lo, hi = erc["i2c_pullup_ohm"]
    i2c_re = re.compile(r"(?<![A-Z])(SDA|SCL)(?![A-Z])", re.I)
    for net, pins in sorted(net_pins.items()):
        names = [nm for _, _, nm in pins]
        m = i2c_re.search(net) or next((i2c_re.search(nm) for nm in names if i2c_re.search(nm)), None)
        if not m or is_gnd(net) or is_power(net):
            continue
        ics = [(r, k) for r, k, _ in pins if re.match(r"^(U|IC)\d", r)]
        if not ics:
            continue
        ok = any(is_power(o) and lo <= (parse_res_ohm(v) or 0) <= hi for _, v, o, _ in two_pin_parts("R", net))
        if not ok:
            rail = "+3V3"
            for r, _ in ics:
                rails = [p["net"] for p in comps[r]["pins"].values() if is_power(p["net"]) and re.search(r"VDD|3V3|VCC", p["name"], re.I)]
                if rails:
                    rail = rails[0]
                    break
            dev = sorted(ics, key=lambda x: len(comps[x[0]]["pins"]))[0]
            if board is not None:  # a through-hole pin on the bus is the easiest place to hang a pull-up
                tht = [(r, k) for r, k, _ in pins if (pd := board.find_pad(f"{r}.{k}")) is not None and pd.kind == "thru_hole"]
                if tht:
                    dev = tht[0]
            iss.add(
                f"I2C-PU-{net}", "i2c_pullup_missing", "error",
                f"I2C line {net} ({m.group(1).upper()}) has no pull-up resistor",
                f"I2C is open-drain: without a 2.2k-10k pull-up to {rail} the bus never rises. Connected: {', '.join(f'{r}.{k}' for r, k, _ in pins)}.",
                nets=[net], refs=sorted({r for r, _, _ in pins}), at=pad_xy(*dev),
                hint=f"add_component resistor 4.7k ({net} to {rail}) near {dev[0]}.{dev[1]}",
            )

    # ---------------------------------------------------------------- ERC: floating pins
    if proj.sch is not None:
        checked.append("unconnected_pins")
        for ref, pin, name in sorted(proj.sch.netlist()["floating"]):
            iss.add(f"NC-{ref}-{pin}", "unconnected_pin", "warning", f"{ref}.{pin} ({name}) is unconnected and has no no-connect flag",
                    refs=[ref], at=pad_xy(ref, pin), hint="connect the pin or mark it no-connect in the schematic")

    if board is not None:
        # ------------------------------------------------------------ sync
        if proj.sch is not None:
            checked.append("schematic_pcb_sync")
            nl = proj.sch.netlist()
            for (ref, pin), net in sorted(nl["pin_net"].items()):
                f = board.fp_by_ref.get(ref)
                if f is None:
                    continue
                pads = [p for p in f.pads if p.num == pin]
                for p in pads:
                    if p.net != net:
                        iss.add(f"SYNC-{ref}-{pin}", "schematic_pcb_mismatch", "error",
                                f"{ref}.{pin}: schematic net {net} but PCB pad is on {p.net or '<no net>'}",
                                refs=[ref], nets=[net], at=(p.x, p.y), hint="re-apply the change with apply_pcb_patch so both files stay in sync")
            sch_refs = {s.ref for s in proj.sch.symbols if not s.virtual}
            pcb_refs = {f.ref for f in board.footprints if "exclude_from_bom" not in f.attrs and not f.ref.startswith("H")}
            for ref in sorted(sch_refs - pcb_refs):
                iss.add(f"SYNC-{ref}", "schematic_pcb_mismatch", "error", f"{ref} is in the schematic but has no footprint on the PCB", refs=[ref])
            for ref in sorted(pcb_refs - sch_refs):
                iss.add(f"SYNC-{ref}", "schematic_pcb_mismatch", "warning", f"{ref} is on the PCB but not in the schematic", refs=[ref])

        # ------------------------------------------------------------ track widths
        checked += ["min_track_width", "power_track_width"]
        by_net: dict[str, list] = {}
        for t in board.tracks:
            by_net.setdefault(t.net, []).append(t)
        pads_by_net: dict[str, list] = {}
        for f in board.footprints:
            for p in f.pads:
                if p.net:
                    pads_by_net.setdefault(p.net, []).append(p)
        for net, tracks in sorted(by_net.items()):
            need = 0.0
            if is_power(net):
                need = pw["high_current_min_width_mm"] if hi_re.search(net) else pw["min_width_mm"]
            fab_min = prof["min_track_mm"]
            thin = []
            for t in tracks:
                if t.width < fab_min - 1e-6:
                    thin.append(t)
                elif need and t.width < need - 1e-6:
                    mx, my = (t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2
                    near_pad = any(math.hypot(mx - p.x, my - p.y) <= pw["neckdown_pad_radius_mm"] for p in pads_by_net.get(net, []))
                    if not (near_pad and t.length <= pw["neckdown_max_len_mm"]):
                        thin.append(t)
            if not thin:
                continue
            wmin = min(t.width for t in thin)
            length = sum(t.length for t in thin)
            worst = max(thin, key=lambda t: t.length)
            at = ((worst.x1 + worst.x2) / 2, (worst.y1 + worst.y2) / 2)
            if need:
                detail = (f"{length:.1f} mm of {net} is routed at {wmin:g} mm. IPC-2221 (1 oz, 10 C rise): {wmin:g} mm carries ~{ipc2221_amps(wmin):.2f} A, "
                          f"{need:g} mm carries ~{ipc2221_amps(need):.2f} A.")
                if wmin < fab_min - 1e-6:
                    detail += f" It is also below the {fab_min} mm fab minimum."
                iss.add(f"PWR-W-{net}", "power_track_width", "error", f"Power net {net} routed at {wmin:g} mm (needs >= {need:g} mm)", detail,
                        nets=[net], at=at, layer=worst.layer, hint=f"set_track_width {net} {need:g}")
            else:
                iss.add(f"TRACK-W-{net}", "min_track_width", "error", f"{net}: track width {wmin:g} mm is below the {fab_min} mm fab minimum",
                        f"{length:.1f} mm of track affected.", nets=[net], at=at, layer=worst.layer, hint=f"set_track_width {net} 0.2")

        # ------------------------------------------------------------ clearance
        checked.append("copper_clearance")
        groups: dict[tuple, list] = {}
        for v in clearance_violations(board, prof["min_clearance_mm"], prof["hole_to_copper_mm"]):
            key = tuple(sorted([f"{v['a'].kind}:{v['a'].net}", f"{v['b'].kind}:{v['b'].net}"]))
            groups.setdefault(key, []).append(v)
        for key, vs in sorted(groups.items(), key=lambda kv: kv[0]):
            v = min(vs, key=lambda q: q["gap_mm"])
            a, b = v["a"], v["b"]
            la = a.label if a.kind == "pad" else f"{a.kind} on {a.net or 'no net'}"
            lb = b.label if b.kind == "pad" else f"{b.kind} on {b.net or 'no net'}"
            via = a if a.kind == "via" else (b if b.kind == "via" else None)
            if via is not None:
                hint = f"move_via at [{via.obj.x:g}, {via.obj.y:g}] (omit 'to' and the tool finds the nearest legal spot)"
            elif "track" in (a.kind, b.kind):
                trk = a if a.kind == "track" else b
                hint = f"reroute_net {trk.net}"
            else:
                hint = "move one of the footprints"
            nets = sorted({n for n in (a.net, b.net) if n})
            tag = "-".join(n.replace("+", "") for n in nets) or "NONET"
            if v["short"]:
                iss.add(f"SHORT-{tag}", "short_circuit", "error", f"Short: {la} touches {lb}",
                        f"{len(vs)} location(s). Different nets must never overlap.", nets=nets, at=(v["x"], v["y"]), layer=v["layer"], hint=hint)
            else:
                iss.add(f"CLR-{tag}", "copper_clearance", "error",
                        f"Clearance {v['gap_mm']:.3f} mm between {la} and {lb} (min {v['required_mm']} mm)",
                        f"{len(vs)} location(s); etch tolerance can bridge this gap.", nets=nets, at=(v["x"], v["y"]), layer=v["layer"], hint=hint)

        # ------------------------------------------------------------ unrouted
        checked.append("unrouted_nets")
        for net, info in sorted(ratsnest(board).items()):
            miss = info["missing"]
            first = miss[0]
            iss.add(f"UNROUTED-{net}", "unrouted_net", "error",
                    f"Net {net} has {len(miss)} unrouted connection(s)",
                    "; ".join(f"{m['from']} -> {m['to']} ({m['dist_mm']} mm airwire)" for m in miss[:4]),
                    nets=[net], at=((first["a"][0] + first["b"][0]) / 2, (first["a"][1] + first["b"][1]) / 2), hint=f"route_net {net}")

        # ------------------------------------------------------------ pour islands
        if not quick:
            checked.append("pour_connectivity")
            for z_net in sorted({z.net for z in board.zones}):
                for grp in (pour[z_net] if pour and pour.get(z_net) is not None else pour_report(board, z_net)):
                    pads = grp["pads"]
                    iss.add(f"POUR-{pads[0].replace('.', '-')}", "pour_island", "error",
                            f"{', '.join(pads[:4])}{' ...' if len(pads) > 4 else ''}: {z_net} pad(s) on an isolated copper-pour island",
                            f"New tracks cut the {z_net} pour here, so the pad(s) no longer reach the main plane (KiCad DRC reports this as unconnected).",
                            refs=sorted({p.split('.')[0] for p in pads}), nets=[z_net], at=(grp["x"], grp["y"]), hint=f"stitch_pour {z_net}")

        # ------------------------------------------------------------ vias / drills
        checked.append("via_geometry")
        bad_vias = [v for v in board.vias if v.drill < prof["min_via_drill_mm"] - 1e-6 or v.size < prof["min_via_diameter_mm"] - 1e-6
                    or (v.size - v.drill) / 2 < prof["min_via_annular_mm"] - 1e-6]
        if bad_vias:
            v = bad_vias[0]
            iss.add("VIA-GEOM", "via_geometry", "error",
                    f"{len(bad_vias)} via(s) below fab limits (e.g. {v.size:g}/{v.drill:g} mm)",
                    f"Minimum via: {prof['min_via_diameter_mm']} mm pad, {prof['min_via_drill_mm']} mm drill, {prof['min_via_annular_mm']} mm annular ring.",
                    at=(v.x, v.y), hint="replace with a 0.6/0.3 mm via")

        # ------------------------------------------------------------ board edge
        checked.append("copper_to_edge")
        ev = edge_violations(board, prof["copper_to_edge_mm"])
        if ev:
            worst = min(ev, key=lambda e: e["gap_mm"])
            sev = "error" if worst["gap_mm"] < prof["abs_copper_to_edge_mm"] else "warning"
            iss.add("EDGE-CU", "copper_to_edge", sev, f"{len(ev)} copper item(s) within {prof['copper_to_edge_mm']} mm of the board edge (closest {worst['gap_mm']} mm)",
                    nets=sorted({e["item"].net for e in ev if e["item"].net}), at=(worst["x"], worst["y"]), hint="reroute away from the edge")

        # ------------------------------------------------------------ placement
        checked += ["placement_grid", "courtyard_overlap"]
        grid = rules["placement"]["grid_mm"]
        fps = [f for f in board.footprints if f.pads and "exclude_from_bom" not in f.attrs]
        for f in fps:
            off = max(abs(f.x / grid - round(f.x / grid)), abs(f.y / grid - round(f.y / grid))) * grid
            if off > 1e-3:
                iss.add(f"PLACE-GRID-{f.ref}", "placement_off_grid", "warning",
                        f"{f.ref} is off the {grid} mm placement grid (at {f.x:g}, {f.y:g})",
                        "Off-grid parts make routing and assembly inspection harder.", refs=[f.ref], at=(f.x, f.y),
                        hint=f"move_footprint {f.ref} (the tool snaps to the grid)")
        courts = [(f, f.court_shape()) for f in board.footprints if f.pads]
        for i in range(len(courts)):
            for j in range(i + 1, len(courts)):
                fa, sa = courts[i]
                fb, sb = courts[j]
                if sa.bbox[0] > sb.bbox[2] or sb.bbox[0] > sa.bbox[2] or sa.bbox[1] > sb.bbox[3] or sb.bbox[1] > sa.bbox[3]:
                    continue
                if shape_dist(sa, sb) < -0.02:
                    iss.add(f"PLACE-OVL-{fa.ref}-{fb.ref}", "courtyard_overlap", "error", f"Courtyards of {fa.ref} and {fb.ref} overlap",
                            refs=[fa.ref, fb.ref], at=((fa.x + fb.x) / 2, (fa.y + fb.y) / 2), hint=f"move_footprint {fb.ref}")

    order = {"error": 0, "warning": 1, "info": 2}
    iss.items.sort(key=lambda it: (order[it["severity"]], it["id"]))
    counts = {s: sum(1 for it in iss.items if it["severity"] == s) for s in ("error", "warning", "info")}
    report = {
        "fab_house": rules["fab"],
        "profile": f"{prof['layers']}-layer, {prof['copper_oz']} oz",
        "pass": counts["error"] == 0,
        "summary": {"errors": counts["error"], "warnings": counts["warning"], "info": counts["info"]},
        "rules_checked": checked,
        "issues": iss.items,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
    }
    proj.write_state("audit.json", report)
    return report
