"""A1 引用与区域解析。

内部一律用 0 基的 ``(row, col)``，只有出现在公式文本里才是 A1 记法。
绝对引用符号 ``$`` 会被保留下来（用于展示与将来的复制填充），
但不影响求值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

MAX_ROW = 1_048_576
MAX_COL = 16_384  # XFD

_CELL_RE = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)(\d{1,7})$")


def col_to_letters(col: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA。"""
    if col < 0:
        raise ValueError("列号不能为负")
    letters = []
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        letters.append(chr(ord("A") + rem))
    return "".join(reversed(letters))


def letters_to_col(letters: str) -> Optional[int]:
    if not letters or not letters.isalpha() or len(letters) > 3:
        return None
    col = 0
    for ch in letters.upper():
        col = col * 26 + (ord(ch) - ord("A") + 1)
    col -= 1
    return col if 0 <= col < MAX_COL else None


def quote_sheet(name: str) -> str:
    """需要时给工作表名加单引号。"""
    if re.fullmatch(r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_.\u4e00-\u9fff]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


@dataclass(frozen=True)
class CellRef:
    row: int
    col: int
    abs_row: bool = False
    abs_col: bool = False
    sheet: Optional[str] = None

    @property
    def a1(self) -> str:
        return f"{col_to_letters(self.col)}{self.row + 1}"

    @property
    def absolute(self) -> str:
        return f"{'$' if self.abs_col else ''}{col_to_letters(self.col)}" \
               f"{'$' if self.abs_row else ''}{self.row + 1}"

    def __str__(self) -> str:  # pragma: no cover - 仅用于调试
        body = self.absolute
        return f"{quote_sheet(self.sheet)}!{body}" if self.sheet else body

    def key(self, default_sheet: str = "Sheet1") -> Tuple[str, int, int]:
        return (self.sheet or default_sheet, self.row, self.col)

    def with_sheet(self, sheet: Optional[str]) -> "CellRef":
        return CellRef(self.row, self.col, self.abs_row, self.abs_col, sheet)


@dataclass(frozen=True)
class RangeRef:
    sheet: Optional[str]
    row1: int
    col1: int
    row2: int
    col2: int

    def __post_init__(self) -> None:
        # 统一成左上 / 右下，公式里写 B2:A1 也能用
        row1, row2 = sorted((self.row1, self.row2))
        col1, col2 = sorted((self.col1, self.col2))
        object.__setattr__(self, "row1", row1)
        object.__setattr__(self, "row2", row2)
        object.__setattr__(self, "col1", col1)
        object.__setattr__(self, "col2", col2)

    @property
    def height(self) -> int:
        return self.row2 - self.row1 + 1

    @property
    def width(self) -> int:
        return self.col2 - self.col1 + 1

    @property
    def size(self) -> int:
        return self.height * self.width

    @property
    def a1(self) -> str:
        start = f"{col_to_letters(self.col1)}{self.row1 + 1}"
        end = f"{col_to_letters(self.col2)}{self.row2 + 1}"
        return f"{start}:{end}"

    def __str__(self) -> str:
        body = self.a1
        return f"{quote_sheet(self.sheet)}!{body}" if self.sheet else body

    def contains(self, row: int, col: int) -> bool:
        return self.row1 <= row <= self.row2 and self.col1 <= col <= self.col2

    def cells(self) -> Iterator[Tuple[int, int]]:
        for row in range(self.row1, self.row2 + 1):
            for col in range(self.col1, self.col2 + 1):
                yield row, col

    def with_sheet(self, sheet: Optional[str]) -> "RangeRef":
        return RangeRef(sheet, self.row1, self.col1, self.row2, self.col2)


def parse_cell(text: str, sheet: Optional[str] = None) -> Optional[CellRef]:
    """解析 ``$B$7`` 这样的单个引用；不合法返回 ``None``。"""
    match = _CELL_RE.match(text.strip())
    if not match:
        return None
    abs_col, letters, abs_row, digits = match.groups()
    col = letters_to_col(letters)
    if col is None:
        return None
    row = int(digits) - 1
    if not 0 <= row < MAX_ROW:
        return None
    return CellRef(row, col, bool(abs_row), bool(abs_col), sheet)


def parse_range(text: str, sheet: Optional[str] = None) -> Optional[RangeRef]:
    """解析 ``A1:B9``，两侧都必须是无工作表的单元格引用。"""
    body = text.strip()
    if body.count(":") != 1:
        return None
    left, right = body.split(":")
    start = parse_cell(left, sheet)
    end = parse_cell(right, sheet)
    if start is None or end is None:
        return None
    return RangeRef(sheet, start.row, start.col, end.row, end.col)


def split_sheet(text: str) -> Tuple[Optional[str], str]:
    """把 ``'销售 表'!A1`` 拆成 ``("销售 表", "A1")``。"""
    if "!" not in text:
        return None, text.strip()
    head, _, rest = text.rpartition("!")
    head = head.strip()
    if len(head) >= 2 and head[0] == "'" and head[-1] == "'":
        head = head[1:-1].replace("''", "'")
    return head or None, rest.strip()
