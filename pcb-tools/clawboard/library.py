"""Self-contained footprint + symbol library ("ClawBoard").

Everything the sample board and ``apply_pcb_patch`` need is generated here, so the
tools never depend on an installed KiCad library.  Footprints are simplified
land patterns - verify against the vendor datasheet before fabricating.
"""
from __future__ import annotations

from .sexpr import S, new_uuid

LIB = "ClawBoard"

# =========================================================================== footprints
# pad: (num, kind, shape, x, y, w, h, drill)   kind: smd | thru_hole | np_thru_hole


def _chip(name, half_pitch, pw, ph, body_w, body_h, desc, polar=False):
    cw, ch = half_pitch + pw / 2 + 0.2, max(ph, body_h) / 2 + 0.2
    g = [
        ("line", -body_w / 2 + 0.25, -body_h / 2 - 0.12, body_w / 2 - 0.25, -body_h / 2 - 0.12, "F.SilkS", 0.12),
        ("line", -body_w / 2 + 0.25, body_h / 2 + 0.12, body_w / 2 - 0.25, body_h / 2 + 0.12, "F.SilkS", 0.12),
        ("rect", -body_w / 2, -body_h / 2, body_w / 2, body_h / 2, "F.Fab", 0.1),
        ("rect", -cw, -ch, cw, ch, "F.CrtYd", 0.05),
    ]
    if polar:
        g.append(("line", -cw - 0.05, -ch, -cw - 0.05, ch, "F.SilkS", 0.12))
    return {
        "name": name,
        "desc": desc,
        "attr": "smd",
        "pads": [
            ("1", "smd", "roundrect", -half_pitch, 0, pw, ph, None),
            ("2", "smd", "roundrect", half_pitch, 0, pw, ph, None),
        ],
        "graphics": g,
        "ref_at": (0, -ch - 0.75),
        "val_at": (0, ch + 0.75),
        "courtyard": (-cw, -ch, cw, ch),
    }


def _header(n):
    pitch = 2.54
    x0 = -(n - 1) * pitch / 2
    pads = [(str(i + 1), "thru_hole", "rect" if i == 0 else "oval", x0 + i * pitch, 0, 1.7, 1.7, 1.0) for i in range(n)]
    w = n * pitch / 2
    return {
        "name": f"PinHeader_1x{n:02d}_P2.54mm",
        "desc": f"1x{n} 2.54 mm pin header",
        "attr": "through_hole",
        "pads": pads,
        "graphics": [
            ("rect", -w, -1.33, w, 1.33, "F.SilkS", 0.12),
            ("line", -w + 2.54, -1.33, -w + 2.54, 1.33, "F.SilkS", 0.12),
            ("rect", -w - 0.45, -1.8, w + 0.45, 1.8, "F.CrtYd", 0.05),
        ],
        "ref_at": (0, -2.5),
        "val_at": (0, 2.6),
        "courtyard": (-w - 0.45, -1.8, w + 0.45, 1.8),
    }


def _usb_c():
    y = -3.25
    pads = []
    # combined power lands (two pad numbers share one land, like the real part)
    for nums, x in ((("A1", "B12"), -3.3), (("A4", "B9"), -2.5), (("B4", "A9"), 2.5), (("B1", "A12"), 3.3)):
        for nm in nums:
            pads.append((nm, "smd", "rect", x, y, 0.6, 1.1, None))
    for nm, x in (("B8", -1.75), ("A5", -1.25), ("B7", -0.75), ("A6", -0.25), ("A7", 0.25), ("B6", 0.75), ("A8", 1.25), ("B5", 1.75)):
        pads.append((nm, "smd", "rect", x, y, 0.3, 1.1, None))
    for x, yy in ((-4.5, -2.5), (4.5, -2.5), (-4.5, 1.5), (4.5, 1.5)):
        pads.append(("S1", "thru_hole", "oval", x, yy, 1.1, 1.9, 0.6))
    return {
        "name": "USB_C_Receptacle_16P",
        "desc": "USB Type-C 2.0 receptacle, 16 pin, mid-mount SMD (simplified HRO TYPE-C-31-M-12 style)",
        "attr": "smd",
        "pads": pads,
        "graphics": [
            ("line", -4.47, -1.2, -4.47, 0.3, "F.SilkS", 0.12),
            ("line", 4.47, -1.2, 4.47, 0.3, "F.SilkS", 0.12),
            ("line", -4.47, 4.7, 4.47, 4.7, "F.SilkS", 0.12),
            ("line", -4.47, 2.8, -4.47, 4.7, "F.SilkS", 0.12),
            ("line", 4.47, 2.8, 4.47, 4.7, "F.SilkS", 0.12),
            ("rect", -4.47, -2.6, 4.47, 4.7, "F.Fab", 0.1),
            ("rect", -5.3, -4.05, 5.3, 4.95, "F.CrtYd", 0.05),
        ],
        "ref_at": (0, -5.0),
        "val_at": (0, 6.0),
        "courtyard": (-5.3, -4.05, 5.3, 4.95),
    }


def _sot223():
    return {
        "name": "SOT-223-3_TabPin2",
        "desc": "SOT-223, 3 pins + tab",
        "attr": "smd",
        "pads": [
            ("1", "smd", "roundrect", -2.3, 3.15, 1.0, 1.9, None),
            ("2", "smd", "roundrect", 0, 3.15, 1.0, 1.9, None),
            ("3", "smd", "roundrect", 2.3, 3.15, 1.0, 1.9, None),
            ("4", "smd", "roundrect", 0, -3.15, 3.3, 1.9, None),
        ],
        "graphics": [
            ("line", -3.35, -1.85, -3.35, 1.85, "F.SilkS", 0.12),
            ("line", 3.35, -1.85, 3.35, 1.85, "F.SilkS", 0.12),
            ("line", -3.35, -1.85, -1.9, -1.85, "F.SilkS", 0.12),
            ("line", 1.9, -1.85, 3.35, -1.85, "F.SilkS", 0.12),
            ("rect", -3.25, -1.75, 3.25, 1.75, "F.Fab", 0.1),
            ("rect", -3.6, -4.35, 3.6, 4.35, "F.CrtYd", 0.05),
        ],
        "ref_at": (-4.6, 0),
        "val_at": (0, 0),
        "courtyard": (-3.6, -4.35, 3.6, 4.35),
    }


def _esp32_s3_wroom():
    pads = []
    for i in range(14):  # left column, pins 1..14 top -> bottom
        pads.append((str(i + 1), "smd", "rect", -8.75, -5.26 + i * 1.27, 1.5, 0.9, None))
    for i in range(12):  # bottom row, pins 15..26 left -> right
        pads.append((str(15 + i), "smd", "rect", -6.985 + i * 1.27, 12.5, 0.9, 1.5, None))
    for i in range(14):  # right column, pins 27..40 bottom -> top
        pads.append((str(27 + i), "smd", "rect", 8.75, 11.25 - i * 1.27, 1.5, 0.9, None))
    pads.append(("41", "smd", "rect", -1.5, 2.46, 3.9, 3.9, None))
    return {
        "name": "ESP32-S3-WROOM-1",
        "desc": "Espressif ESP32-S3-WROOM-1 module, 18 x 25.5 mm (simplified land pattern)",
        "attr": "smd",
        "pads": pads,
        "graphics": [
            ("rect", -9, -12.75, 9, 12.75, "F.Fab", 0.1),
            ("line", -9, -12.75, 9, -12.75, "F.SilkS", 0.12),
            ("line", -9, -12.75, -9, -6.1, "F.SilkS", 0.12),
            ("line", 9, -12.75, 9, -6.1, "F.SilkS", 0.12),
            ("line", -9, 12.1, -9, 12.75, "F.SilkS", 0.12),
            ("line", 9, 12.1, 9, 12.75, "F.SilkS", 0.12),
            ("line", -9, 12.75, -7.7, 12.75, "F.SilkS", 0.12),
            ("line", 7.7, 12.75, 9, 12.75, "F.SilkS", 0.12),
            ("line", -9, -6.75, 9, -6.75, "F.SilkS", 0.12),  # antenna boundary
            ("rect", -9.75, -13.0, 9.75, 13.5, "F.CrtYd", 0.05),
        ],
        "ref_at": (0, -9.6),
        "val_at": (0, -8.0),
        "courtyard": (-9.75, -13.0, 9.75, 13.5),
    }


def _bme280():
    # LGA-8 2.5 x 2.5 mm, 0.65 mm pitch.  1-4 on the left (top->bottom), 5-8 on the right (bottom->top)
    pads = []
    for i in range(4):
        pads.append((str(i + 1), "smd", "rect", -1.025, -0.975 + i * 0.65, 0.5, 0.35, None))
    for i in range(4):
        pads.append((str(5 + i), "smd", "rect", 1.025, 0.975 - i * 0.65, 0.5, 0.35, None))
    return {
        "name": "Bosch_LGA-8_2.5x2.5mm_P0.65mm",
        "desc": "Bosch BME280 LGA-8 (simplified)",
        "attr": "smd",
        "pads": pads,
        "graphics": [
            ("line", -1.35, -1.4, 1.35, -1.4, "F.SilkS", 0.12),
            ("line", -1.35, 1.4, 1.35, 1.4, "F.SilkS", 0.12),
            ("circle", -1.75, -1.3, 0.1, "F.SilkS", 0.2),
            ("rect", -1.25, -1.25, 1.25, 1.25, "F.Fab", 0.1),
            ("rect", -1.55, -1.55, 1.55, 1.55, "F.CrtYd", 0.05),
        ],
        "ref_at": (0, -2.3),
        "val_at": (0, 2.3),
        "courtyard": (-1.55, -1.55, 1.55, 1.55),
    }


def _htssop16():
    pads = []
    for i in range(8):  # 1..8 left, top -> bottom
        pads.append((str(i + 1), "smd", "roundrect", -2.85, -2.275 + i * 0.65, 1.45, 0.4, None))
    for i in range(8):  # 9..16 right, bottom -> top
        pads.append((str(9 + i), "smd", "roundrect", 2.85, 2.275 - i * 0.65, 1.45, 0.4, None))
    pads.append(("17", "smd", "rect", 0, 0, 2.4, 3.2, None))
    return {
        "name": "HTSSOP-16-1EP_4.4x5mm_P0.65mm",
        "desc": "HTSSOP-16 with exposed pad (TI PWP)",
        "attr": "smd",
        "pads": pads,
        "graphics": [
            ("line", -2.2, -2.7, 2.2, -2.7, "F.SilkS", 0.12),
            ("line", -2.2, 2.7, 2.2, 2.7, "F.SilkS", 0.12),
            ("circle", -3.3, -2.9, 0.1, "F.SilkS", 0.2),
            ("rect", -2.2, -2.5, 2.2, 2.5, "F.Fab", 0.1),
            ("rect", -3.85, -2.95, 3.85, 2.95, "F.CrtYd", 0.05),
        ],
        "ref_at": (0, -3.7),
        "val_at": (0, 3.7),
        "courtyard": (-3.85, -2.95, 3.85, 2.95),
    }


def _hole():
    return {
        "name": "MountingHole_2.7mm_M2.5",
        "desc": "M2.5 mounting hole, non-plated",
        "attr": "exclude_from_pos_files exclude_from_bom",
        "pads": [("", "np_thru_hole", "circle", 0, 0, 2.7, 2.7, 2.7)],
        "graphics": [("circle", 0, 0, 2.45, "F.CrtYd", 0.05), ("circle", 0, 0, 2.2, "Cmts.User", 0.15)],
        "ref_at": (0, -3.2),
        "val_at": (0, 3.2),
        "courtyard": (-2.45, -2.45, 2.45, 2.45),
        "hide_ref": True,
    }


FOOTPRINTS: dict[str, dict] = {}
for _fp in (
    _chip("R_0402", 0.51, 0.54, 0.64, 1.0, 0.5, "Resistor 0402 (1005 metric)"),
    _chip("C_0402", 0.48, 0.56, 0.62, 1.0, 0.5, "Capacitor 0402 (1005 metric)"),
    _chip("R_0603", 0.825, 0.8, 0.95, 1.6, 0.8, "Resistor 0603 (1608 metric)"),
    _chip("C_0603", 0.775, 0.9, 0.95, 1.6, 0.8, "Capacitor 0603 (1608 metric)"),
    _chip("R_0805", 0.9125, 1.025, 1.4, 2.0, 1.25, "Resistor 0805 (2012 metric)"),
    _chip("C_0805", 0.95, 1.0, 1.45, 2.0, 1.25, "Capacitor 0805 (2012 metric)"),
    _chip("LED_0603", 0.7875, 0.875, 0.95, 1.6, 0.8, "LED 0603 (1608 metric)", polar=True),
    _header(3),
    _header(4),
    _usb_c(),
    _sot223(),
    _esp32_s3_wroom(),
    _bme280(),
    _htssop16(),
    _hole(),
):
    FOOTPRINTS[_fp["name"]] = _fp


def _stroke(width):
    return [S("stroke"), [S("width"), width], [S("type"), S("solid")]]


def _font(size=1.0, thick=0.15):
    return [S("effects"), [S("font"), [S("size"), size, size], [S("thickness"), thick]]]


def footprint_node(fp_name, ref, value, x, y, rot=0.0, pad_nets=None, net_token=None, sym_uuid=None, pin_types=None, pin_funcs=None):
    """Build a ``(footprint ...)`` node placed at (x, y, rot) on F.Cu.

    ``net_token(name)`` returns the ``(net ...)`` list for a pad (board specific).
    """
    spec = FOOTPRINTS[fp_name]
    pad_nets = pad_nets or {}
    r = float(rot) % 360
    node = [S("footprint"), f"{LIB}:{fp_name}", [S("layer"), "F.Cu"], [S("uuid"), new_uuid()], [S("at"), x, y, r]]
    node.append([S("descr"), spec["desc"]])

    def prop(name, text, at, layer, hide=False, size=1.0):
        p = [S("property"), name, text, [S("at"), at[0], at[1], r], [S("layer"), layer]]
        if hide:
            p.append([S("hide"), S("yes")])
        p += [[S("uuid"), new_uuid()], _font(size, 0.15 if size >= 1 else 0.12)]
        return p

    small = fp_name[:2] in ("R_", "C_") or fp_name.startswith("LED")
    node.append(prop("Reference", ref, spec["ref_at"], "F.SilkS", hide=bool(spec.get("hide_ref")), size=0.8 if small else 1.0))
    node.append(prop("Value", value, spec["val_at"], "F.Fab", size=0.8 if small else 1.0))
    node.append(prop("Footprint", f"{LIB}:{fp_name}", (0, 0), "F.Fab", hide=True))
    node.append(prop("Datasheet", "", (0, 0), "F.Fab", hide=True))
    node.append(prop("Description", spec["desc"], (0, 0), "F.Fab", hide=True))
    if sym_uuid:
        node.append([S("path"), f"/{sym_uuid}"])
    node.append([S("attr")] + [S(a) for a in spec["attr"].split()])
    for g in spec["graphics"]:
        kind = g[0]
        if kind == "line":
            _, x1, y1, x2, y2, layer, w = g
            node.append([S("fp_line"), [S("start"), x1, y1], [S("end"), x2, y2], _stroke(w), [S("layer"), layer], [S("uuid"), new_uuid()]])
        elif kind == "rect":
            _, x1, y1, x2, y2, layer, w = g
            node.append([S("fp_rect"), [S("start"), x1, y1], [S("end"), x2, y2], _stroke(w), [S("fill"), S("none")], [S("layer"), layer], [S("uuid"), new_uuid()]])
        elif kind == "circle":
            _, cx, cy, rad, layer, w = g
            node.append([S("fp_circle"), [S("center"), cx, cy], [S("end"), cx + rad, cy], _stroke(w), [S("fill"), S("none")], [S("layer"), layer], [S("uuid"), new_uuid()]])
    for pnum, kind, shape, px, py, pw, ph, drill in spec["pads"]:
        pad = [S("pad"), pnum, S(kind), S(shape), [S("at"), px, py, r], [S("size"), pw, ph]]
        if drill:
            pad.append([S("drill"), drill])
        if kind == "smd":
            pad.append([S("layers"), "F.Cu", "F.Paste", "F.Mask"])
        elif kind == "thru_hole":
            pad.append([S("layers"), "*.Cu", "*.Mask"])
            pad.append([S("remove_unused_layers"), S("no")])
        else:
            pad.append([S("layers"), "*.Cu", "*.Mask"])
        if shape == "roundrect":
            pad.append([S("roundrect_rratio"), 0.25])
        net = pad_nets.get(pnum)
        if net and net_token and kind != "np_thru_hole":
            pad.append(net_token(net))
        if pin_funcs and pnum in pin_funcs:
            pad.append([S("pinfunction"), pin_funcs[pnum]])
        if pin_types and pnum in pin_types:
            pad.append([S("pintype"), pin_types[pnum]])
        pad.append([S("uuid"), new_uuid()])
        node.append(pad)
    return node


def footprint_module_text(fp_name) -> list:
    """A stand-alone ``.kicad_mod`` tree for the project-local footprint library."""
    node = footprint_node(fp_name, "REF**", fp_name, 0, 0)
    out = [S("footprint"), fp_name, [S("version"), 20240108], [S("generator"), "clawboard"], [S("generator_version"), "8.0"]]
    for c in node[2:]:
        if isinstance(c, list) and c[0] in ("at", "uuid", "path"):
            continue
        out.append(c)
    return out


# =========================================================================== symbols
PIN_LEN = 2.54
P = 2.54


def _sfont(hide=False):
    e = [S("effects"), [S("font"), [S("size"), 1.27, 1.27]]]
    if hide:
        e.append([S("hide"), S("yes")])
    return e


def _pin(etype, x, y, angle, name, number, length=PIN_LEN):
    return [
        S("pin"), S(etype), S("line"), [S("at"), x, y, angle], [S("length"), length],
        [S("name"), name, _sfont()], [S("number"), number, _sfont()],
    ]


def _ic_symbol(name, prefix, desc, left=(), right=(), top=(), bottom=(), min_width=4):
    """Rectangular IC symbol.  Each side is a list of (number, name, electrical_type) or None (gap)."""
    rows = max(len(left), len(right), 1)
    cols = max(len(top), len(bottom), 0)
    longest = max([len(p[1]) for p in list(left) + list(right) if p] + [1])
    half_w = max(min_width, (cols + 1) // 2 + 1, int(longest * 0.55) + 2) * P
    half_w = round(half_w / P) * P
    top_y = (rows - 1) * P / 2 + P
    top_y = round(top_y / 1.27) * 1.27
    bot_y = top_y - (rows + 1) * P
    pins = []
    for i, p in enumerate(left):
        if p:
            pins.append(_pin(p[2], -half_w - PIN_LEN, top_y - (i + 1) * P, 0, p[1], p[0]))
    for i, p in enumerate(right):
        if p:
            pins.append(_pin(p[2], half_w + PIN_LEN, top_y - (i + 1) * P, 180, p[1], p[0]))
    x0 = -(len(top) - 1) * P / 2
    x0 = round(x0 / P) * P
    for i, p in enumerate(top):
        if p:
            pins.append(_pin(p[2], x0 + i * P, top_y + PIN_LEN, 270, p[1], p[0]))
    x0 = -(len(bottom) - 1) * P / 2
    x0 = round(x0 / P) * P
    for i, p in enumerate(bottom):
        if p:
            pins.append(_pin(p[2], x0 + i * P, bot_y - PIN_LEN, 90, p[1], p[0]))
    body = [
        S("rectangle"), [S("start"), -half_w, top_y], [S("end"), half_w, bot_y],
        [S("stroke"), [S("width"), 0.254], [S("type"), S("default")]], [S("fill"), [S("type"), S("background")]],
    ]
    return {
        "name": name, "prefix": prefix, "desc": desc, "graphics": [body], "pins": pins,
        "ref_at": (-half_w, top_y + 1.27 + (PIN_LEN + 6 if top else 0)), "val_at": (-half_w, top_y + 3.81 + (PIN_LEN + 6 if top else 0)),
        "pin_names_offset": 1.016, "hide_pin_numbers": False,
    }


def _two_pin(name, prefix, desc, graphics, pin_len, pin_type="passive", names=("~", "~")):
    return {
        "name": name, "prefix": prefix, "desc": desc, "graphics": graphics,
        "pins": [_pin(pin_type, 0, 3.81, 270, names[0], "1", pin_len), _pin(pin_type, 0, -3.81, 90, names[1], "2", pin_len)],
        "ref_at": (2.54, 1.27), "val_at": (2.54, -1.27), "pin_names_offset": 0, "hide_pin_numbers": True,
        "hide_pin_names": True,
    }


def _poly(pts, width=0.254, fill="none"):
    return [
        S("polyline"), [S("pts")] + [[S("xy"), x, y] for x, y in pts],
        [S("stroke"), [S("width"), width], [S("type"), S("default")]], [S("fill"), [S("type"), S(fill)]],
    ]


_R = _two_pin("R", "R", "Resistor", [[
    S("rectangle"), [S("start"), -1.016, 2.54], [S("end"), 1.016, -2.54],
    [S("stroke"), [S("width"), 0.254], [S("type"), S("default")]], [S("fill"), [S("type"), S("none")]]]], 1.27)
_C = _two_pin("C", "C", "Unpolarized capacitor", [_poly([(-2.032, 0.762), (2.032, 0.762)], 0.508), _poly([(-2.032, -0.762), (2.032, -0.762)], 0.508)], 3.048)
_LED = _two_pin("LED", "D", "Light emitting diode", [
    _poly([(-1.27, 1.27), (1.27, 1.27)]), _poly([(-1.27, -1.27), (1.27, -1.27), (0, 1.27), (-1.27, -1.27)]),
], 2.54, names=("K", "A"))
_FLAG = {
    "name": "PWR_FLAG", "prefix": "#FLG", "desc": "Marks a net as driven (silences ERC power-input checks)",
    "graphics": [_poly([(0, 0), (0, 1.27), (-1.016, 1.905), (0, 2.54), (1.016, 1.905), (0, 1.27)], 0)],
    "pins": [_pin("power_out", 0, 0, 90, "pwr", "1", 0)],
    "ref_at": (0, 1.905), "val_at": (0, 3.81), "pin_names_offset": 0, "hide_pin_numbers": True, "hide_pin_names": True,
    "virtual": True,
}

_USB = _ic_symbol(
    "USB_C_Receptacle_USB2.0", "J", "USB Type-C receptacle, USB 2.0 data + CC",
    right=[("A4", "VBUS", "passive"), ("A9", "VBUS", "passive"), ("B4", "VBUS", "passive"), ("B9", "VBUS", "passive"), None,
           ("A5", "CC1", "bidirectional"), ("B5", "CC2", "bidirectional"), None,
           ("A6", "D+", "bidirectional"), ("B6", "D+", "bidirectional"), ("A7", "D-", "bidirectional"), ("B7", "D-", "bidirectional"), None,
           ("A8", "SBU1", "bidirectional"), ("B8", "SBU2", "bidirectional")],
    bottom=[("A1", "GND", "passive"), ("A12", "GND", "passive"), ("B1", "GND", "passive"), ("B12", "GND", "passive"), ("S1", "SHIELD", "passive")],
    min_width=4,
)

_ESP_NAMES = [
    "GND", "3V3", "EN", "IO4", "IO5", "IO6", "IO7", "IO15", "IO16", "IO17", "IO18", "IO8", "IO19/USB_D-", "IO20/USB_D+",
    "IO3", "IO46", "IO9", "IO10", "IO11", "IO12", "IO13", "IO14", "IO21", "IO47", "IO48", "IO45",
    "IO0", "IO35", "IO36", "IO37", "IO38", "IO39", "IO40", "IO41", "IO42", "RXD0", "TXD0", "IO2", "IO1", "GND",
]


def _esp_pin(i):
    name = _ESP_NAMES[i]
    etype = "power_in" if name in ("GND", "3V3") else ("input" if name == "EN" else "bidirectional")
    return (str(i + 1), name, etype)


_ESP = _ic_symbol(
    "ESP32-S3-WROOM-1", "U", "ESP32-S3 Wi-Fi + BLE module, 2.4 GHz, native USB",
    left=[_esp_pin(i) for i in range(1, 20)],
    right=[_esp_pin(i) for i in range(20, 39)],
    bottom=[_esp_pin(0), _esp_pin(39), ("41", "GND", "power_in")],
    min_width=6,
)

_LDO = _ic_symbol(
    "AMS1117-3.3", "U", "1 A low-dropout regulator, 3.3 V fixed",
    left=[("3", "VIN", "power_in")], right=[("2", "VOUT", "power_out"), ("4", "VOUT", "passive")], bottom=[("1", "GND", "power_in")],
    min_width=3,
)

_BME = _ic_symbol(
    "BME280", "U", "Bosch humidity / pressure / temperature sensor, I2C or SPI",
    left=[("8", "VDD", "power_in"), ("6", "VDDIO", "power_in"), ("2", "CSB", "input"), ("5", "SDO", "bidirectional")],
    right=[("3", "SDI/SDA", "bidirectional"), ("4", "SCK/SCL", "input")],
    bottom=[("1", "GND", "power_in"), ("7", "GND", "power_in")],
    min_width=4,
)

_DRV = _ic_symbol(
    "DRV8833", "U", "TI dual H-bridge motor driver, 1.5 A, 2.7-10.8 V",
    left=[("16", "AIN1", "input"), ("15", "AIN2", "input"), ("9", "BIN1", "input"), ("10", "BIN2", "input"),
          ("1", "nSLEEP", "input"), ("8", "nFAULT", "open_collector")],
    right=[("2", "AOUT1", "output"), ("4", "AOUT2", "output"), ("7", "BOUT1", "output"), ("5", "BOUT2", "output"),
           ("3", "AISEN", "passive"), ("6", "BISEN", "passive")],
    top=[("12", "VM", "power_in"), ("14", "VINT", "power_out"), ("11", "VCP", "passive")],
    bottom=[("13", "GND", "power_in"), ("17", "EP", "passive")],
    min_width=4,
)


def _conn(n):
    return _ic_symbol(f"Conn_01x{n:02d}", "J", f"Generic 1x{n} connector",
                      right=[(str(i + 1), f"Pin_{i + 1}", "passive") for i in range(n)], min_width=2)


SYMBOLS: dict[str, dict] = {s["name"]: s for s in (_R, _C, _LED, _FLAG, _USB, _ESP, _LDO, _BME, _DRV, _conn(3), _conn(4))}


def lib_symbol_node(sym_name, with_lib_prefix=True) -> list:
    """``(symbol "ClawBoard:R" ...)`` for ``lib_symbols`` (or a .kicad_sym file)."""
    s = SYMBOLS[sym_name]
    full = f"{LIB}:{sym_name}" if with_lib_prefix else sym_name
    node = [S("symbol"), full]
    if s.get("hide_pin_numbers"):
        node.append([S("pin_numbers"), S("hide")])
    pn = [S("pin_names"), [S("offset"), s.get("pin_names_offset", 1.016)]]
    if s.get("hide_pin_names"):
        pn.append(S("hide"))
    node.append(pn)
    virtual = bool(s.get("virtual"))
    node += [[S("exclude_from_sim"), S("no")], [S("in_bom"), S("no" if virtual else "yes")], [S("on_board"), S("no" if virtual else "yes")]]
    node.append([S("property"), "Reference", s["prefix"], [S("at"), s["ref_at"][0], s["ref_at"][1], 0], _sfont(hide=virtual)])
    node.append([S("property"), "Value", sym_name, [S("at"), s["val_at"][0], s["val_at"][1], 0], _sfont()])
    node.append([S("property"), "Footprint", "", [S("at"), 0, 0, 0], _sfont(hide=True)])
    node.append([S("property"), "Datasheet", "~", [S("at"), 0, 0, 0], _sfont(hide=True)])
    node.append([S("property"), "Description", s["desc"], [S("at"), 0, 0, 0], _sfont(hide=True)])
    node.append([S("symbol"), f"{sym_name}_0_1"] + [list(g) for g in s["graphics"]])
    node.append([S("symbol"), f"{sym_name}_1_1"] + [list(p) for p in s["pins"]])
    return node


def symbol_pins(sym_name) -> list[dict]:
    """Pin geometry in library coordinates (Y up)."""
    out = []
    for p in SYMBOLS[sym_name]["pins"]:
        out.append({"type": str(p[1]), "x": p[3][1], "y": p[3][2], "angle": p[3][3], "name": p[5][1], "number": p[6][1]})
    return out


# default part choices used by apply_pcb_patch -> add_component
PASSIVES = {
    "resistor": {"symbol": "R", "prefix": "R", "footprints": {"0402": "R_0402", "0603": "R_0603", "0805": "R_0805"}},
    "capacitor": {"symbol": "C", "prefix": "C", "footprints": {"0402": "C_0402", "0603": "C_0603", "0805": "C_0805"}},
    "led": {"symbol": "LED", "prefix": "D", "footprints": {"0603": "LED_0603"}},
}
