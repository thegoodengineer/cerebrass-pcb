#!/usr/bin/env python3
"""Generate ./sample_board - an ESP32-S3 USB-C sensor + motor controller with six classic bugs.

    python pcb-tools/make_sample_board.py [output_dir]

The board is placed by hand (table below), routed by ClawBoard's own router, and then
six realistic mistakes are injected:

  1. USB-C CC1 and CC2 tied together on one 5.1k resistor
  2. no 100 nF decoupling capacitor at the ESP32 3V3 pin
  3. no pull-ups on I2C SDA / SCL
  4. +5V_VBUS routed at 0.1 mm instead of 0.5 mm
  5. a GND stitching via 0.05 mm away from a signal track
  6. UART_TX left unrouted, and the BME280 decoupling cap placed far away and off-grid
"""
from __future__ import annotations

import json
import math
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clawboard import library  # noqa: E402
from clawboard.geom import Shape, shape_dist  # noqa: E402
from clawboard.pcb import CU_LAYERS, Board, Cu, ratsnest  # noqa: E402
from clawboard.router import RouteGrid, route_net  # noqa: E402
from clawboard.patch import widen_tracks  # noqa: E402
from clawboard.pour import stitch_pour  # noqa: E402
from clawboard.sch import Schematic  # noqa: E402
from clawboard.sexpr import S, dumps, new_uuid  # noqa: E402

NAME = "sample_board"
OX, OY = 100.0, 60.0  # board origin on the KiCad page
BW, BH = 64.0, 44.0
L = library.LIB

ESP_NETS = {
    "1": "GND", "2": "+3V3", "3": "MCU_EN", "4": "MOT_AIN1", "5": "MOT_AIN2", "6": "MOT_BIN1", "7": "MOT_BIN2",
    "8": "MOT_NSLEEP", "9": "MOT_NFAULT", "11": "I2C_SCL", "12": "I2C_SDA", "13": "USB_DN", "14": "USB_DP",
    "36": "UART_RX", "37": "UART_TX", "38": "LED_STATUS", "40": "GND", "41": "GND",
}

# ref, symbol, value, footprint, (x, y, rot) on the board [mm from board origin], (x, y) on the sheet, pin nets
PARTS = [
    ("J1", "USB_C_Receptacle_USB2.0", "USB-C", "USB_C_Receptacle_16P", (4.75, 22.0, 270), (40.64, 83.82), {
        "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND", "S1": "GND",
        "A4": "+5V_VBUS", "A9": "+5V_VBUS", "B4": "+5V_VBUS", "B9": "+5V_VBUS",
        "A5": "USB_CC", "B5": "USB_CC",                       # BUG 1: CC1 + CC2 share one net / one resistor
        "A6": "USB_DP", "B6": "USB_DP", "A7": "USB_DN", "B7": "USB_DN"}),
    ("R1", "R", "5.1k", "R_0603", (13.0, 27.5, 0), (86.36, 88.9), {"1": "USB_CC", "2": "GND"}),
    ("U2", "AMS1117-3.3", "AMS1117-3.3", "SOT-223-3_TabPin2", (17.0, 8.0, 180), (127.0, 50.8), {"1": "GND", "2": "+3V3", "3": "+5V_VBUS", "4": "+3V3"}),
    ("C1", "C", "10uF", "C_0805", (11.0, 5.0, 180), (99.06, 50.8), {"1": "+5V_VBUS", "2": "GND"}),
    ("C2", "C", "22uF", "C_0805", (23.0, 11.0, 0), (157.48, 50.8), {"1": "+3V3", "2": "GND"}),
    ("U1", "ESP32-S3-WROOM-1", "ESP32-S3-WROOM-1", "ESP32-S3-WROOM-1", (47.5, 12.75, 0), (269.24, 111.76), ESP_NETS),
    ("C4", "C", "10uF", "C_0805", (35.5, 5.0, 0), (228.6, 50.8), {"1": "+3V3", "2": "GND"}),
    # BUG 2: the 100 nF HF decoupler for U1.2 (C3) is missing
    ("R5", "R", "10k", "R_0603", (32.0, 9.0, 0), (241.3, 50.8), {"1": "+3V3", "2": "MCU_EN"}),
    ("C9", "C", "1uF", "C_0603", (32.0, 11.5, 0), (254.0, 50.8), {"1": "MCU_EN", "2": "GND"}),
    ("U3", "BME280", "BME280", "Bosch_LGA-8_2.5x2.5mm_P0.65mm", (28.0, 14.0, 0), (127.0, 114.3), {
        "1": "GND", "2": "+3V3", "3": "I2C_SDA", "4": "I2C_SCL", "5": "GND", "6": "+3V3", "7": "GND", "8": "+3V3"}),
    # BUG 6b: decoupler for U3 sits ~15 mm away and off the placement grid
    ("C5", "C", "100nF", "C_0603", (18.37, 29.61, 0), (93.98, 114.3), {"1": "+3V3", "2": "GND"}),
    # BUG 3: no pull-ups (R3 / R4) on I2C_SDA / I2C_SCL
    ("J4", "Conn_01x04", "I2C", "PinHeader_1x04_P2.54mm", (28.0, 4.0, 0), (170.18, 114.3), {"1": "GND", "2": "+3V3", "3": "I2C_SDA", "4": "I2C_SCL"}),
    ("U4", "DRV8833", "DRV8833", "HTSSOP-16-1EP_4.4x5mm_P0.65mm", (27.0, 33.0, 0), (127.0, 180.34), {
        "1": "MOT_NSLEEP", "2": "MOT_AOUT1", "3": "GND", "4": "MOT_AOUT2", "5": "MOT_BOUT2", "6": "GND", "7": "MOT_BOUT1",
        "8": "MOT_NFAULT", "9": "MOT_BIN1", "10": "MOT_BIN2", "11": "DRV_VCP", "12": "+5V_VBUS", "13": "GND", "14": "DRV_VINT",
        "15": "MOT_AIN2", "16": "MOT_AIN1", "17": "GND"}),
    ("C6", "C", "10uF", "C_0805", (33.5, 33.25, 0), (170.18, 162.56), {"1": "+5V_VBUS", "2": "GND"}),
    ("C7", "C", "2.2uF", "C_0603", (33.5, 31.0, 0), (182.88, 162.56), {"1": "DRV_VINT", "2": "GND"}),
    ("C8", "C", "10nF", "C_0603", (32.75, 36.0, 270), (195.58, 162.56), {"1": "DRV_VCP", "2": "+5V_VBUS"}),
    ("R7", "R", "10k", "R_0603", (21.0, 39.0, 0), (208.28, 162.56), {"1": "+3V3", "2": "MOT_NFAULT"}),
    ("J2", "Conn_01x04", "MOTORS", "PinHeader_1x04_P2.54mm", (12.0, 39.5, 0), (180.34, 200.66), {
        "1": "MOT_AOUT1", "2": "MOT_AOUT2", "3": "MOT_BOUT2", "4": "MOT_BOUT1"}),
    ("J3", "Conn_01x03", "UART", "PinHeader_1x03_P2.54mm", (50.0, 39.5, 0), (345.44, 83.82), {"1": "GND", "2": "UART_TX", "3": "UART_RX"}),
    ("R6", "R", "1k", "R_0603", (59.75, 28.0, 90), (345.44, 127.0), {"1": "LED_STATUS", "2": "LED_A"}),
    ("D1", "LED", "GREEN", "LED_0603", (59.75, 33.0, 90), (358.14, 127.0), {"1": "GND", "2": "LED_A"}),
]
HOLES = [(3.0, 3.0), (61.0, 3.0), (3.0, 41.0), (61.0, 41.0)]
NOTES = [
    ("USB-C POWER + DATA", 22.0, 50.0), ("3V3 REGULATOR", 92.0, 26.0), ("BME280 SENSOR (I2C)", 88.0, 104.0),
    ("DRV8833 MOTOR DRIVER", 92.0, 146.0), ("ESP32-S3 MCU", 226.0, 26.0), ("UART / STATUS", 330.0, 60.0),
    ("AGENT PATCHES", 286.0, 186.0),
]
# (net, width, start_pads, max_connections).  Local hops around the fine-pitch ICs go first,
# then the MCU escapes, then the long rails - the order a human layout engineer would use.
ROUTE_PLAN = [
    ("+3V3", 0.4, {"U3.2", "U3.6", "U3.8"}, 2), ("+5V_VBUS", 0.5, {"U4.12"}, 2),
    ("DRV_VINT", 0.3, None, None), ("DRV_VCP", 0.25, None, None), ("MCU_EN", 0.25, None, None), ("LED_A", 0.25, None, None),
    ("MOT_BOUT1", 0.3, None, None), ("MOT_BOUT2", 0.3, None, None), ("MOT_AOUT2", 0.3, None, None), ("MOT_AOUT1", 0.3, None, None),
    ("USB_DP", 0.25, None, None), ("USB_DN", 0.25, None, None), ("USB_CC", 0.25, None, None),
    ("MOT_NFAULT", 0.25, None, None), ("MOT_NSLEEP", 0.25, None, None), ("MOT_BIN2", 0.25, None, None), ("MOT_BIN1", 0.25, None, None),
    ("MOT_AIN2", 0.25, None, None), ("MOT_AIN1", 0.25, None, None),
    ("I2C_SCL", 0.25, None, None), ("I2C_SDA", 0.25, None, None),
    ("+5V_VBUS", 0.5, None, None), ("+3V3", 0.4, None, None),
    ("UART_RX", 0.25, None, None), ("LED_STATUS", 0.25, None, None),
    # UART_TX intentionally not routed (BUG 6a)
]
# Copper-free landing zones (board mm) kept open for parts a reviewer would want to add/move.
RESERVED = [(30.3, 12.4, 33.3, 14.7), (35.0, 6.6, 37.6, 8.2)]


def build_schematic(path: Path) -> Schematic:
    root = new_uuid()
    tree = [
        S("kicad_sch"), [S("version"), 20231120], [S("generator"), "clawboard"], [S("generator_version"), "8.0"],
        [S("uuid"), root], [S("paper"), "A3"],
        [S("title_block"), [S("title"), "ESP32-S3 USB-C Sensor + Motor Controller"], [S("date"), time.strftime("%Y-%m-%d")],
         [S("rev"), "A"], [S("company"), "ClawBoard sample (contains intentional bugs)"]],
        [S("lib_symbols")],
        [S("sheet_instances"), [S("path"), "/", [S("page"), "1"]]],
    ]
    path.write_text(dumps(tree), encoding="utf-8", newline="\n")
    sch = Schematic(path)
    for text, x, y in NOTES:
        sch._append([S("text"), text, [S("exclude_from_sim"), S("no")], [S("at"), x, y, 0],
                     [S("effects"), [S("font"), [S("size"), 2.54, 2.54], [S("bold"), S("yes")]], [S("justify"), S("left"), S("bottom")]],
                     [S("uuid"), new_uuid()]])
    for ref, sym, value, fp, _, (sx, sy), nets in PARTS:
        sch.add_symbol(sym, ref, value, f"{L}:{fp}", sx, sy, nets, project=NAME)
    sch.add_symbol("PWR_FLAG", "#FLG01", "PWR_FLAG", "", 38.1, 35.56, {"1": "+5V_VBUS"}, project=NAME)
    sch.add_symbol("PWR_FLAG", "#FLG02", "PWR_FLAG", "", 58.42, 35.56, {"1": "GND"}, project=NAME)
    sch.save()
    return sch


LAYERS = [
    (0, "F.Cu", "signal", None), (31, "B.Cu", "signal", None), (32, "B.Adhes", "user", "B.Adhesive"), (33, "F.Adhes", "user", "F.Adhesive"),
    (34, "B.Paste", "user", None), (35, "F.Paste", "user", None), (36, "B.SilkS", "user", "B.Silkscreen"), (37, "F.SilkS", "user", "F.Silkscreen"),
    (38, "B.Mask", "user", None), (39, "F.Mask", "user", None), (40, "Dwgs.User", "user", "User.Drawings"), (41, "Cmts.User", "user", "User.Comments"),
    (42, "Eco1.User", "user", "User.Eco1"), (43, "Eco2.User", "user", "User.Eco2"), (44, "Edge.Cuts", "user", None), (45, "Margin", "user", None),
    (46, "B.CrtYd", "user", "B.Courtyard"), (47, "F.CrtYd", "user", "F.Courtyard"), (48, "B.Fab", "user", None), (49, "F.Fab", "user", None),
]


def build_board(path: Path, sch: Schematic) -> Board:
    nets = ["GND"]
    for p in PARTS:
        for n in p[6].values():
            if n not in nets:
                nets.append(n)
    net_num = {n: i + 1 for i, n in enumerate(nets)}

    def tok(name):
        return [S("net"), net_num[name], name]

    tree = [
        S("kicad_pcb"), [S("version"), 20240108], [S("generator"), "clawboard"], [S("generator_version"), "8.0"],
        [S("general"), [S("thickness"), 1.6], [S("legacy_teardrops"), S("no")]], [S("paper"), "A4"],
        [S("title_block"), [S("title"), "ESP32-S3 USB-C Sensor + Motor Controller"], [S("rev"), "A"], [S("company"), "ClawBoard sample"]],
        [S("layers")] + [[k, name, S(kind)] + ([alias] if alias else []) for k, name, kind, alias in LAYERS],
        [S("setup"), [S("pad_to_mask_clearance"), 0], [S("allow_soldermask_bridges_in_footprints"), S("no")]],
        [S("net"), 0, ""],
    ] + [[S("net"), k, n] for n, k in net_num.items()]
    for ref, sym, value, fp, (x, y, r), _, pnets in PARTS:
        s = sch.by_ref[ref]
        types = {p.number: p.etype for p in s.pins}
        tree.append(library.footprint_node(fp, ref, value, OX + x, OY + y, r, pnets, tok, s.uuid, pin_types=types,
                                           pin_funcs={p.number: p.name for p in s.pins if p.name != "~"}))
    for i, (x, y) in enumerate(HOLES):
        tree.append(library.footprint_node("MountingHole_2.7mm_M2.5", f"H{i + 1}", "M2.5", OX + x, OY + y, 0))
    tree.append([S("gr_rect"), [S("start"), OX, OY], [S("end"), OX + BW, OY + BH], [S("stroke"), [S("width"), 0.1], [S("type"), S("default")]],
                 [S("fill"), S("none")], [S("layer"), "Edge.Cuts"], [S("uuid"), new_uuid()]])
    tree.append([S("gr_text"), "ClawBoard demo rev A", [S("at"), OX + 32, OY + 42.6, 0], [S("layer"), "F.SilkS"], [S("uuid"), new_uuid()],
                 [S("effects"), [S("font"), [S("size"), 0.8, 0.8], [S("thickness"), 0.12]]]])
    for layer in CU_LAYERS:
        m = 0.3
        tree.append([
            S("zone"), [S("net"), net_num["GND"]], [S("net_name"), "GND"], [S("layer"), layer], [S("uuid"), new_uuid()],
            [S("name"), f"GND_{layer[0]}"], [S("hatch"), S("edge"), 0.5], [S("connect_pads"), S("yes"), [S("clearance"), 0.2]],
            [S("min_thickness"), 0.2], [S("filled_areas_thickness"), S("no")],
            [S("fill"), S("yes"), [S("thermal_gap"), 0.3], [S("thermal_bridge_width"), 0.3], [S("island_removal_mode"), 0]],
            [S("polygon"), [S("pts"), [S("xy"), OX + m, OY + m], [S("xy"), OX + BW - m, OY + m], [S("xy"), OX + BW - m, OY + BH - m], [S("xy"), OX + m, OY + BH - m]]],
        ])
    path.write_text(dumps(tree), encoding="utf-8", newline="\n")
    return Board(path)


def make_grid(board: Board) -> RouteGrid:
    grid = RouteGrid(board)
    for x1, y1, x2, y2 in RESERVED:
        sh = Shape.rect(OX + (x1 + x2) / 2, OY + (y1 + y2) / 2, x2 - x1, y2 - y1)
        grid.add(Cu(sh, CU_LAYERS, None, "hole", "reserved", None))
    return grid


def ground_links(board: Board) -> None:
    """Tie the DRV8833's GND / sense pins straight to its exposed pad, as the datasheet layout does."""
    ep = board.find_pad("U4.17")
    for pin in ("3", "6", "13"):
        p = board.find_pad(f"U4.{pin}")
        board.add_track("GND", "F.Cu", p.x, p.y, ep.x + (0.9 if p.x > ep.x else -0.9), p.y, 0.25)
    # BME280: join its three ground pads with a trunk under the package (no vias under the sensor)
    p1, p5, p7 = (board.find_pad(f"U3.{n}") for n in ("1", "5", "7"))
    tx = p7.x - 0.625
    for a, b in (((p7.x, p7.y), (tx, p7.y)), ((tx, p7.y), (tx, p5.y)), ((tx, p5.y), (p5.x, p5.y)),
                 ((p1.x, p1.y), (tx, p1.y)), ((tx, p1.y), (tx, p7.y))):
        board.add_track("GND", "F.Cu", a[0], a[1], b[0], b[1], 0.2)


def route_all(board: Board) -> list[str]:
    ground_links(board)
    grid = make_grid(board)
    problems = []
    for net, width, start, limit in ROUTE_PLAN:
        t0 = time.perf_counter()
        res = route_net(board, net, width=width, grid=grid, only_pads=start, limit=limit, effort=350_000, weight=1.0)
        if res["failed"] and width > 0.25:
            res2 = route_net(board, net, width=0.2, clearance=0.15, grid=make_grid(board), only_pads=start, limit=limit, effort=350_000, weight=1.0)
            ids = set(res2["new"])
            widen_tracks(board, [t for t in board.tracks if t.uuid in ids], width)
            res["failed"] = res2["failed"]
            res["routed"] += res2["routed"]
            res["length_mm"] = round(res["length_mm"] + res2["length_mm"], 2)
            grid = make_grid(board)
        flag = "" if not res["failed"] else f"  ** FAILED {res['failed']}"
        print(f"  {net:<12} {res['routed']} conn  {res['length_mm']:>6} mm  {res['vias']} vias  {time.perf_counter() - t0:5.2f}s{flag}")
        if res["failed"]:
            problems.append(net)
    return problems


def add_stitching(board: Board, pitch: float = 7.0) -> int:
    n = 0
    y = OY + 4.5
    while y < OY + BH - 3:
        x = OX + 4.5
        while x < OX + BW - 3:
            sh = Shape.circle(x, y, 0.3)
            ok = True
            for c in board.copper():
                if c.net == "GND" and c.kind == "pad" and c.obj.kind == "smd" and shape_dist(sh, c.shape) < 0.1:
                    ok = False  # no via-in-pad
                    break
                if c.net == "GND" and c.kind != "hole":
                    continue
                if shape_dist(sh, c.shape) < 0.35:
                    ok = False
                    break
            if ok and not any(shape_dist(sh, f.court_shape()) < 0.1 for f in board.footprints):
                board.add_via("GND", x, y, 0.6, 0.3)
                n += 1
            x += pitch
        y += pitch
    return n


def inject_clearance_bug(board: Board) -> "tuple[float, float, str] | None":
    """BUG 5: a GND via 0.05 mm from a signal track."""
    gap = 0.05
    cands = [t for t in board.tracks if t.layer == "F.Cu" and t.net in ("I2C_SCL", "I2C_SDA", "UART_RX", "MOT_AIN1", "MOT_BIN1")
             and t.length >= 2.0 and (abs(t.x1 - t.x2) < 1e-6 or abs(t.y1 - t.y2) < 1e-6)]
    cands.sort(key=lambda t: (("I2C_SCL", "I2C_SDA", "UART_RX", "MOT_AIN1", "MOT_BIN1").index(t.net), -t.length))
    for t in cands:
        mx, my = (t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2
        off = 0.3 + t.width / 2 + gap
        for sx, sy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            if abs(t.x1 - t.x2) < 1e-6 and sy:   # vertical track -> offset in x
                continue
            if abs(t.y1 - t.y2) < 1e-6 and sx:   # horizontal track -> offset in y
                continue
            x, y = round(mx + sx * off, 3), round(my + sy * off, 3)
            sh = Shape.circle(x, y, 0.3)
            ok = True
            for c in board.copper():
                if c.obj is t or (c.net == "GND" and c.kind != "hole" and not (c.kind == "pad" and c.obj.kind == "smd")):
                    continue
                if shape_dist(sh, c.shape) < 0.3:
                    ok = False
                    break
            if ok and not any(shape_dist(sh, f.court_shape()) < 0.2 for f in board.footprints):
                board.add_via("GND", x, y, 0.6, 0.3)
                return x, y, t.net
    return None


def write_project_files(out: Path) -> None:
    pro = {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2, "silk_line_width": 0.12, "silk_text_size_h": 1.0, "silk_text_size_v": 1.0},
            "rules": {"min_clearance": 0.127, "min_track_width": 0.127, "min_via_diameter": 0.5, "min_via_annular_width": 0.1,
                      "min_through_hole_diameter": 0.3, "min_copper_edge_clearance": 0.3, "min_hole_clearance": 0.2,
                      "min_hole_to_hole": 0.25, "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1, "solder_mask_to_copper_clearance": 0.0},
            "track_widths": [0.0, 0.25, 0.4, 0.5],
            "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.6, "drill": 0.3}],
        }},
        "meta": {"filename": f"{NAME}.kicad_pro", "version": 1},
        "net_settings": {
            "classes": [
                {"name": "Default", "clearance": 0.127, "track_width": 0.25, "via_diameter": 0.6, "via_drill": 0.3, "microvia_diameter": 0.3,
                 "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "wire_width": 6, "bus_width": 12,
                 "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
                {"name": "Power", "clearance": 0.127, "track_width": 0.5, "via_diameter": 0.6, "via_drill": 0.3, "microvia_diameter": 0.3,
                 "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "wire_width": 6, "bus_width": 12,
                 "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
            ],
            "meta": {"version": 3},
            "netclass_patterns": [{"netclass": "Power", "pattern": "+5V_VBUS"}, {"netclass": "Power", "pattern": "+3V3"}],
        },
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [],
        "text_variables": {},
    }
    (out / f"{NAME}.kicad_pro").write_text(json.dumps(pro, indent=2), encoding="utf-8")
    (out / f"{NAME}.kicad_dru").write_text(
        '(version 1)\n\n'
        '# Mirrors ClawBoard\'s power-width rule so KiCad\'s own DRC reports it too.\n'
        '# 0.5 mm nominal; 0.4 mm is the floor allowed for short neck-downs into fine-pitch pads.\n'
        '(rule "High-current rail width"\n  (condition "A.NetName == \'+5V_VBUS\'")\n  (constraint track_width (min 0.4mm)))\n',
        encoding="utf-8")
    (out / "sym-lib-table").write_text(
        f'(sym_lib_table\n  (version 7)\n  (lib (name "{L}")(type "KiCad")(uri "${{KIPRJMOD}}/{L}.kicad_sym")(options "")(descr "ClawBoard sample symbols"))\n)\n', encoding="utf-8")
    (out / "fp-lib-table").write_text(
        f'(fp_lib_table\n  (version 7)\n  (lib (name "{L}")(type "KiCad")(uri "${{KIPRJMOD}}/{L}.pretty")(options "")(descr "ClawBoard sample footprints"))\n)\n', encoding="utf-8")
    symlib = [S("kicad_symbol_lib"), [S("version"), 20231120], [S("generator"), "clawboard"], [S("generator_version"), "8.0"]]
    symlib += [library.lib_symbol_node(n, with_lib_prefix=False) for n in library.SYMBOLS]
    (out / f"{L}.kicad_sym").write_text(dumps(symlib), encoding="utf-8", newline="\n")
    pretty = out / f"{L}.pretty"
    pretty.mkdir(exist_ok=True)
    for n in library.FOOTPRINTS:
        (pretty / f"{n}.kicad_mod").write_text(dumps(library.footprint_module_text(n)), encoding="utf-8", newline="\n")


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "sample_board"
    out.mkdir(parents=True, exist_ok=True)
    state = out / ".clawboard"
    if state.exists():
        shutil.rmtree(state)
    for old in out.glob("*.kicad_p*"):
        old.unlink()
    print(f"generating {out}")
    sch = build_schematic(out / f"{NAME}.kicad_sch")
    board = build_board(out / f"{NAME}.kicad_pcb", sch)
    print("routing:")
    problems = route_all(board)
    print(f"  stitching vias: {add_stitching(board)}")
    # BUG 4: the whole VBUS rail drawn with a 0.1 mm signal-trace width
    for t in board.tracks:
        if t.net == "+5V_VBUS":
            board.set_track_width(t, 0.1)
    bug5 = inject_clearance_bug(board)
    print(f"  clearance bug: {bug5}")
    st = stitch_pour(board, "GND")
    print(f"  pour repair: {len(st['vias'])} via(s), {st['links']} link(s), stranded {st['stranded']}")
    board.save()
    write_project_files(out)
    left = {n: len(v["missing"]) for n, v in ratsnest(Board(out / f"{NAME}.kicad_pcb")).items()}
    print(f"unrouted after generation (expected: UART_TX only): {left}")
    if problems or bug5 is None or set(left) != {"UART_TX"} or st["stranded"]:
        print("!! generator did not reach the intended state")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
