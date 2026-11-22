"""End-to-end tests: the sample board has exactly the six bug classes, and the tools fix all of them.

    python -m unittest discover -s pcb-tools/tests -v
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pcb-tools"))

from clawboard import tools  # noqa: E402
from clawboard.audit import ipc2221_amps, parse_cap_nf, parse_res_ohm  # noqa: E402
from clawboard.autofix import plan_fixes  # noqa: E402
from clawboard.pcb import Board, clearance_violations, ratsnest  # noqa: E402
from clawboard.sch import Schematic  # noqa: E402
from clawboard.sexpr import Sym, dumps, looks_intact, parse  # noqa: E402

SAMPLE = ROOT / "sample_board"
EXPECTED_BUGS = {
    "usb_c_cc_pulldown",      # 1  CC1 / CC2 tied together
    "decoupling_missing",     # 2  no 100 nF at the MCU supply pin
    "i2c_pullup_missing",     # 3  SDA / SCL without pull-ups
    "power_track_width",      # 4  VBUS at 0.1 mm
    "copper_clearance",       # 5  GND via 0.05 mm from a signal track
    "unrouted_net",           # 6a UART_TX
    "decoupling_too_far",     # 6b sensor decoupler far away ...
    "placement_off_grid",     #    ... and off the placement grid
}


class Parsing(unittest.TestCase):
    def test_sexpr_round_trip(self):
        text = '(a (b "x y" 1.5) (c "quote \\" here") (d))'
        tree = parse(text)
        self.assertEqual(tree[0], Sym("a"))
        self.assertEqual(tree[1][1], "x y")
        self.assertEqual(parse(dumps(tree)), tree)
        self.assertTrue(looks_intact(dumps(tree), "a"))
        self.assertFalse(looks_intact("(a (b)", "a"))

    def test_values(self):
        self.assertAlmostEqual(parse_cap_nf("100nF"), 100)
        self.assertAlmostEqual(parse_cap_nf("0.1uF"), 100)
        self.assertAlmostEqual(parse_cap_nf("4u7"), 4700)
        self.assertAlmostEqual(parse_res_ohm("5.1k"), 5100)
        self.assertAlmostEqual(parse_res_ohm("4k7"), 4700)
        self.assertAlmostEqual(ipc2221_amps(0.5), 1.45, places=1)

    def test_board_files_survive_a_round_trip(self):
        for name in ("sample_board.kicad_pcb", "sample_board.kicad_sch"):
            tree = parse((SAMPLE / name).read_text(encoding="utf-8"))
            self.assertEqual(parse(dumps(tree)), tree)


class SampleBoard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clawboard_test_"))
        cls.dir = cls.tmp / "board"
        shutil.copytree(SAMPLE, cls.dir, ignore=shutil.ignore_patterns(".clawboard"))
        (cls.dir / ".clawboard").mkdir()
        (cls.dir / ".clawboard" / "config.json").write_text(json.dumps({
            "svg_out": str(cls.tmp / "board.svg"), "sch_svg_out": str(cls.tmp / "sch.svg")}))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_1_inspect_is_compact(self):
        res = tools.inspect_kicad_project(str(self.dir))
        self.assertEqual(res["board"]["size_mm"], [64.0, 44.0])
        self.assertIn("U1 ESP32-S3-WROOM-1", " ".join(res["components"]))
        self.assertEqual(res["unrouted"][0].split(":")[0], "UART_TX")
        self.assertLess(len(json.dumps(res)), 9000, "inspect output must stay small enough for the context window")

    def test_2_schematic_and_board_agree(self):
        nl = Schematic(self.dir / "sample_board.kicad_sch").netlist()
        board = Board(self.dir / "sample_board.kicad_pcb")
        for (ref, pin), net in nl["pin_net"].items():
            for pad in board.fp_by_ref[ref].pads:
                if pad.num == pin:
                    self.assertEqual(pad.net, net, f"{ref}.{pin}")
        self.assertEqual(nl["floating"], [])

    def test_3_audit_finds_exactly_the_planted_bugs(self):
        report = tools.run_dfm_and_erc_audit(str(self.dir))
        self.assertFalse(report["pass"])
        self.assertEqual({i["rule"] for i in report["issues"]}, EXPECTED_BUGS)
        self.assertEqual(report["summary"]["errors"], 8)
        self.assertTrue((self.tmp / "board.svg").read_text(encoding="utf-8").rstrip().endswith("</svg>"))

    def test_4_patch_fixes_everything(self):
        report = tools.run_dfm_and_erc_audit(str(self.dir))
        ops = plan_fixes(report)
        res = tools.apply_pcb_patch(str(self.dir), ops)
        self.assertEqual(res["failed"], 0, res["results"])
        self.assertTrue(res["audit"]["pass"], res["audit"])
        counts = [r["errors_after"] for r in res["results"]]
        self.assertEqual(counts, sorted(counts, reverse=True), "no operation may make the board worse")
        board = Board(self.dir / "sample_board.kicad_pcb")
        self.assertEqual(ratsnest(board), {})
        self.assertEqual(clearance_violations(board, 0.127), [])
        self.assertTrue(all(t.width >= 0.4 for t in board.tracks if t.net == "+5V_VBUS"))
        nl = Schematic(self.dir / "sample_board.kicad_sch").netlist()
        self.assertNotEqual(nl["pin_net"][("J1", "A5")], nl["pin_net"][("J1", "B5")])
        self.assertTrue((self.dir / ".clawboard" / "original" / "sample_board.kicad_pcb").exists(), "pristine copy kept for rollback")

    def test_5_bad_requests_are_rejected_cleanly(self):
        res = tools.apply_pcb_patch(str(self.dir), [{"op": "teleport"}, {"op": "route_net", "net": "NOPE"},
                                                    {"op": "set_track_width", "net": "+3V3", "width": 0.05}])
        self.assertEqual(res["applied"], 0)
        self.assertTrue(all(not r["ok"] and r["error"] for r in res["results"]))
        self.assertIn("error", tools.call_tool("inspect_kicad_project", {"project_dir": str(self.tmp / "missing")}))


class McpServer(unittest.TestCase):
    def test_protocol(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "inspect_kicad_project", "arguments": {"project_dir": str(SAMPLE)}}},
        ]
        p = subprocess.run([sys.executable, str(ROOT / "pcb-tools" / "mcp_server.py")], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        out = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        self.assertEqual([o["id"] for o in out], [1, 2, 3])
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "clawboard-pcb")
        names = [t["name"] for t in out[1]["result"]["tools"]]
        self.assertEqual(names, ["inspect_kicad_project", "run_dfm_and_erc_audit", "apply_pcb_patch", "render_board_svg"])
        payload = json.loads(out[2]["result"]["content"][0]["text"])
        self.assertEqual(payload["project"], "sample_board")


if __name__ == "__main__":
    unittest.main()
