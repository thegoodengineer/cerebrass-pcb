"""Tiny, dependency-free KiCad S-expression reader/writer.

Atoms:
  * ``Sym``  -> a bare token  (``F.Cu`` is quoted in files, ``smd`` / ``0.25`` are not)
  * ``str``  -> a quoted string
  * ``int`` / ``float`` / ``bool`` are accepted when *writing* (builders use them freely)

Numbers are kept as ``Sym`` when reading so a load/save round trip never
re-formats values the tool did not touch.
"""
from __future__ import annotations

import re
import uuid as _uuid

__all__ = ["Sym", "parse", "dumps", "fmt", "num", "find", "find_all", "val", "new_uuid", "S", "looks_intact"]


class Sym(str):
    """A bare (unquoted) S-expression token."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Sym({str.__repr__(self)})"


S = Sym

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')
_UNESC = re.compile(r"\\(.)", re.S)
_ESC_MAP = {"n": "\n", "t": "\t", "r": "\r"}


def parse(text: str):
    """Parse one S-expression document into nested Python lists."""
    stack: list[list] = [[]]
    pos, n = 0, len(text)
    while True:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise ValueError(f"S-expression syntax error near offset {pos}")
            break
        pos = m.end()
        if m.group(1):
            new: list = []
            stack[-1].append(new)
            stack.append(new)
        elif m.group(2):
            stack.pop()
            if not stack:
                raise ValueError("unbalanced ')'")
        elif m.group(3) is not None:
            raw = m.group(3)
            if "\\" in raw:
                raw = _UNESC.sub(lambda mm: _ESC_MAP.get(mm.group(1), mm.group(1)), raw)
            stack[-1].append(raw)
        else:
            stack[-1].append(Sym(m.group(4)))
        if pos >= n:
            break
    if len(stack) != 1 or len(stack[0]) != 1:
        raise ValueError("unbalanced S-expression")
    return stack[0][0]


def fmt(x: float) -> str:
    """Format a number the way KiCad does (max 6 decimals, no trailing zeros)."""
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _atom(a) -> str:
    if isinstance(a, Sym):
        return str(a)
    if isinstance(a, str):
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, int):
        return str(a)
    if isinstance(a, float):
        return fmt(a)
    raise TypeError(f"cannot serialise {a!r}")


def _inline(node) -> str:
    if not isinstance(node, list):
        return _atom(node)
    return "(" + " ".join(_inline(c) for c in node) + ")"


def _measure(node, memo: dict) -> int:
    """Length of the one-line form of every list node, computed once (bottom-up)."""
    if not isinstance(node, list):
        return len(_atom(node))
    total = 1 + len(node)
    for c in node:
        total += _measure(c, memo)
    memo[id(node)] = total
    return total


def _write(node, level: int, out: list[str], memo: dict) -> None:
    pad = "\t" * level
    if not isinstance(node, list):
        out.append(pad + _atom(node))
        return
    nested = [c for c in node if isinstance(c, list)]
    # short, shallow nodes stay on one line (pads, segments, properties ...)
    if not nested or (memo[id(node)] <= 96 and not any(memo[id(c)] > 60 and any(isinstance(g, list) for g in c) for c in nested)):
        out.append(pad + _inline(node))
        return
    i = 0
    head: list[str] = []
    while i < len(node) and not isinstance(node[i], list):
        head.append(_atom(node[i]))
        i += 1
    out.append(pad + "(" + " ".join(head))
    for c in node[i:]:
        _write(c, level + 1, out, memo)
    out.append(pad + ")")


def dumps(node) -> str:
    out: list[str] = []
    memo: dict = {}
    _measure(node, memo)
    _write(node, 0, out, memo)
    return "\n".join(out) + "\n"


_STRINGS = re.compile(r'"(?:[^"\\]|\\.)*"')


def looks_intact(text: str, head: str) -> bool:
    """Cheap structural check before a file is replaced: right root token, balanced parentheses."""
    if not text.lstrip().startswith("(" + head):
        return False
    bare = _STRINGS.sub("", text)
    return bare.count("(") == bare.count(")") and bare.rstrip().endswith(")")


# --------------------------------------------------------------------------- helpers
def find(node, name: str):
    """First child list whose head is ``name`` (or None)."""
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def find_all(node, name: str) -> list:
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def val(node, name: str, default=None, idx: int = 1):
    """Value ``idx`` of child ``name`` (``(width 0.25)`` -> ``'0.25'``)."""
    c = find(node, name)
    if c is None or len(c) <= idx:
        return default
    return c[idx]


def new_uuid() -> str:
    return str(_uuid.uuid4())
