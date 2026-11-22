"""A KiCad project directory: locates the board + schematic and owns the ``.clawboard`` state folder."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from .pcb import Board
from .sch import Schematic
from .sexpr import dumps, looks_intact

STATE_DIR = ".clawboard"


def replace_file(tmp: Path, dst: Path) -> None:
    """Atomic rename that tolerates Windows readers briefly holding the destination open."""
    for attempt in range(40):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            time.sleep(0.01 + 0.005 * attempt)
    dst.write_bytes(tmp.read_bytes())
    tmp.unlink(missing_ok=True)


class Project:
    def __init__(self, project_dir):
        self.dir = Path(project_dir).expanduser().resolve()
        if self.dir.is_file():
            self.dir = self.dir.parent
        if not self.dir.is_dir():
            raise FileNotFoundError(f"project directory not found: {project_dir}")
        pro = sorted(self.dir.glob("*.kicad_pro"))
        stem = pro[0].stem if pro else None
        self.pcb_path = self._pick("*.kicad_pcb", stem)
        self.sch_path = self._pick("*.kicad_sch", stem)
        if self.pcb_path is None and self.sch_path is None:
            raise FileNotFoundError(f"no .kicad_pcb or .kicad_sch found in {self.dir}")
        self.name = stem or (self.pcb_path or self.sch_path).stem
        self._board: "Board | None" = None
        self._sch: "Schematic | None" = None

    def _pick(self, pattern: str, stem: "str | None") -> "Path | None":
        files = sorted(p for p in self.dir.glob(pattern) if not p.name.startswith("_"))
        if not files:
            return None
        for f in files:
            if stem and f.stem == stem:
                return f
        return files[0]

    @property
    def board(self) -> "Board | None":
        if self._board is None and self.pcb_path is not None:
            self._board = Board(self.pcb_path)
        return self._board

    @property
    def sch(self) -> "Schematic | None":
        if self._sch is None and self.sch_path is not None:
            self._sch = Schematic(self.sch_path)
        return self._sch

    def save(self, board: bool = True, sch: bool = True) -> None:
        """Write the models back.  A file is only replaced after its new text passes a structural
        check (a corrupt write is caught before it can clobber a good design), and the pristine
        original is kept once under ``.clawboard/original`` for rollback."""
        todo = []
        if board and self._board is not None:
            todo.append((self._board, self.pcb_path, "kicad_pcb"))
        if sch and self._sch is not None:
            todo.append((self._sch, self.sch_path, "kicad_sch"))
        for model, path, head in todo:
            text = dumps(model.tree)
            if not looks_intact(text, head):
                raise ValueError(f"refusing to write {path.name}: serialised file failed the integrity check")
            keep = self.state_dir / "original" / path.name
            if not keep.exists() and path.exists():
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, keep)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8", newline="\n")
            replace_file(tmp, path)

    # ------------------------------------------------------------------ state
    @property
    def state_dir(self) -> Path:
        d = self.dir / STATE_DIR
        d.mkdir(exist_ok=True)
        return d

    def read_state(self, name: str, default=None):
        p = self.dir / STATE_DIR / name
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write_state(self, name: str, data) -> None:
        p = self.state_dir / name
        # plain overwrite: one file operation instead of create + rename (readers tolerate a torn read)
        text = json.dumps(data, separators=(",", ":"))
        for attempt in range(40):
            try:
                p.write_text(text, encoding="utf-8")
                return
            except PermissionError:
                time.sleep(0.01 + 0.005 * attempt)

    def mark_changed(self, changes: dict[str, str]) -> None:
        """uuid/ref -> 'added' | 'modified' | 'moved'.  The renderer highlights these."""
        if not changes:
            return
        cur = self.read_state("changes.json", None) or {"seq": 0, "items": {}}
        cur["seq"] += 1
        for key, kind in changes.items():
            cur["items"][key] = [kind, cur["seq"]]
        self.write_state("changes.json", cur)

    def log_event(self, event: dict) -> None:
        event = {"ts": round(time.time() * 1000), **event}
        with (self.state_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")
