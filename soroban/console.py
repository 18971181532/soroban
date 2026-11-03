"""终端输出：中英文混排对齐、颜色与表格。"""

from __future__ import annotations

import os
import sys
import unicodedata
from typing import Iterable, List, Optional, Sequence

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"


def enable_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def paint(text: str, *codes: str) -> str:
    if not codes or not supports_color():
        return text
    return "".join(codes) + text + RESET


def display_width(text: str) -> int:
    total = 0
    for char in str(text):
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def truncate(text: str, width: int) -> str:
    text = str(text)
    if display_width(text) <= width:
        return text
    out: List[str] = []
    used = 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + size > width - 1:
            break
        out.append(char)
        used += size
    return "".join(out) + "…"


def pad(text: str, width: int, align: str = "left") -> str:
    text = truncate(str(text), width)
    gap = width - display_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def table(
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    aligns: Optional[Sequence[str]] = None,
    *,
    max_width: int = 26,
    indent: str = "  ",
) -> str:
    rows = [[str(cell) for cell in row] for row in rows]
    columns = len(headers)
    aligns = list(aligns or ["left"] * columns)
    widths = []
    for index in range(columns):
        cells = [headers[index]] + [row[index] if index < len(row) else "" for row in rows]
        widths.append(min(max(display_width(cell) for cell in cells), max_width))

    def line(values: Sequence[str], *, header: bool = False) -> str:
        pieces = []
        for index, value in enumerate(values):
            text = pad(value, widths[index], aligns[index])
            pieces.append(paint(text, BOLD) if header else text)
        return indent + "  ".join(pieces).rstrip()

    output = [line(headers, header=True)]
    output.append(indent + "  ".join("─" * width for width in widths))
    output.extend(line(row) for row in rows)
    return "\n".join(output)


def rule(width: int = 64, char: str = "─") -> str:
    return char * width
