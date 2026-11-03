"""工作簿：单元格存储、CSV / JSON 读写与对外门面。

分层是刻意的：``Engine`` 只关心图与数值，``Workbook`` 负责“用户说的
A1 到底是哪张表的哪一格”。所有对外入口（CLI、HTTP、Python API）都从
这里进，保证语义只有一份。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .engine import Engine, Key, RecalcStats
from .errors import FormulaError
from .parser import Node, parse_formula, unparse
from .refs import CellRef, RangeRef, col_to_letters, parse_cell, split_sheet
from .values import Err, Value, display, format_number, is_number

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


@dataclass
class Cell:
    """一个格子的原始输入与解析结果。"""

    raw: str = ""
    kind: str = "blank"          # blank / number / text / bool / formula / bad
    ast: Optional[Node] = None
    value: Value = None
    message: str = ""


class Sheet:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cells: Dict[Tuple[int, int], Cell] = {}

    def cell(self, row: int, col: int) -> Optional[Cell]:
        return self.cells.get((row, col))

    def bounds(self) -> Tuple[int, int]:
        """返回 (最大行, 最大列)，空格表返回 (-1, -1)。"""
        if not self.cells:
            return -1, -1
        return (
            max(row for row, _ in self.cells),
            max(col for _, col in self.cells),
        )

    def __len__(self) -> int:
        return len(self.cells)


class Workbook:
    def __init__(
        self,
        sheets: Sequence[str] | str = ("Sheet1",),
        *,
        iterative: bool = False,
        max_iterations: int = 100,
        tolerance: float = 1e-9,
    ) -> None:
        names = [sheets] if isinstance(sheets, str) else list(sheets)
        if not names:
            names = ["Sheet1"]
        self.sheets: Dict[str, Sheet] = {}
        for name in names:
            if name in self.sheets:
                raise ValueError(f"工作表重名：{name}")
            self.sheets[name] = Sheet(name)
        self.engine = Engine(self)
        self.engine.iterative = iterative
        self.engine.max_iterations = max_iterations
        self.engine.tolerance = tolerance

    # -- 工作表 -----------------------------------------------------------
    @property
    def sheet_names(self) -> List[str]:
        return list(self.sheets)

    @property
    def default_sheet(self) -> str:
        return next(iter(self.sheets))

    def resolve_sheet(self, name: Optional[str]) -> str:
        if name is None:
            return self.default_sheet
        if name in self.sheets:
            return name
        folded = name.casefold()
        for existing in self.sheets:
            if existing.casefold() == folded:
                return existing
        raise ValueError(f"没有工作表“{name}”")

    def add_sheet(self, name: str) -> Sheet:
        if name in self.sheets:
            raise ValueError(f"工作表已存在：{name}")
        sheet = Sheet(name)
        self.sheets[name] = sheet
        return sheet

    def sheet(self, name: Optional[str] = None) -> Sheet:
        return self.sheets[self.resolve_sheet(name)]

    # -- 单元格定位 -------------------------------------------------------
    def target_key(self, target: str | CellRef) -> Key:
        if isinstance(target, CellRef):
            sheet = self.resolve_sheet(target.sheet)
            return (sheet, target.row, target.col)
        text = str(target).strip()
        if not text:
            raise ValueError("单元格地址不能为空")
        sheet_name, body = split_sheet(text)
        ref = parse_cell(body)
        if ref is None:
            raise ValueError(f"无效的单元格地址：{target}")
        sheet = self.resolve_sheet(sheet_name)
        return (sheet, ref.row, ref.col)

    def cell_at(self, key: Key) -> Optional[Cell]:
        sheet = self.sheets.get(key[0])
        if sheet is None:
            return None
        return sheet.cell(key[1], key[2])

    def _key_label(self, key: Key) -> str:
        return f"{key[0]}!{col_to_letters(key[2])}{key[1] + 1}"

    # -- 写入与读取 -------------------------------------------------------
    def store(self, target: str | CellRef, raw: Optional[str], strict: bool = True) -> Key:
        """写入但不触发重算，供批量导入使用。"""
        key = self.target_key(target)
        self._store_key(key, raw, strict)
        return key

    def _store_key(self, key: Key, raw: Optional[str], strict: bool = True) -> None:
        sheet = self.sheets[key[0]]
        position = (key[1], key[2])

        if raw is None or (isinstance(raw, str) and raw == ""):
            sheet.cells.pop(position, None)
            self.engine.detach(key)
            return

        raw = str(raw)
        stripped = raw.strip()
        if stripped == "":
            sheet.cells.pop(position, None)
            self.engine.detach(key)
            return

        if stripped.startswith("="):
            try:
                ast = parse_formula(stripped)
            except FormulaError as exc:
                if strict:
                    raise
                cell = Cell(raw=raw, kind="bad", message=exc.message)
            else:
                cell = Cell(raw=raw, kind="formula", ast=ast)
        else:
            value, kind = _parse_literal(raw)
            cell = Cell(raw=raw, kind=kind, value=value)

        sheet.cells[position] = cell
        self.engine.attach(key, cell)

    def set(self, target: str | CellRef, raw: Optional[str], strict: bool = True) -> RecalcStats:
        key = self.store(target, raw, strict)
        return self.engine.recalculate({key})

    def update(self, changes: Mapping[str, Optional[str]], strict: bool = False) -> RecalcStats:
        roots = set()
        for target, raw in changes.items():
            roots.add(self.store(target, raw, strict))
        return self.engine.recalculate(roots)

    def clear(self, target: str | CellRef) -> RecalcStats:
        return self.set(target, None)

    def get(self, target: str | CellRef) -> Value:
        key = self.target_key(target)
        value = self.engine.values.get(key)
        if value is None and key in self.engine.bad:
            return Err("#PARSE!", self.engine.bad[key])
        return value

    def raw(self, target: str | CellRef) -> str:
        cell = self.cell_at(self.target_key(target))
        return cell.raw if cell is not None else ""

    def formula(self, target: str | CellRef) -> Optional[str]:
        cell = self.cell_at(self.target_key(target))
        if cell is None or cell.kind != "formula" or cell.ast is None:
            return None
        return "=" + unparse(cell.ast)

    def text(self, target: str | CellRef) -> str:
        return display(self.get(target))

    def recalculate(self) -> RecalcStats:
        return self.engine.recalculate()

    @property
    def stats(self) -> RecalcStats:
        return self.engine.stats

    # -- 审计 -------------------------------------------------------------
    def audit(self, target: str | CellRef, depth: int = 2) -> Dict[str, object]:
        return self.engine.audit(self.target_key(target), depth=depth)

    def explain(self, target: str | CellRef, depth: int = 3) -> str:
        return self.engine.explain(self.target_key(target), depth=depth)

    def precedents(self, target: str | CellRef) -> List[str]:
        key = self.target_key(target)
        cells, ranges = self.engine.precedents_of(key)
        labels = [self._key_label(item) for item in cells]
        labels.extend(str(rng) for rng in ranges)
        return labels

    def dependents(self, target: str | CellRef) -> List[str]:
        key = self.target_key(target)
        return [self._key_label(item) for item in sorted(self.engine.dependents_of(key))]

    # -- 序列化 -----------------------------------------------------------
    def to_dict(self) -> Dict[str, object]:
        sheets: Dict[str, Dict[str, str]] = {}
        for name, sheet in self.sheets.items():
            rows: Dict[str, str] = {}
            for (row, col), cell in sorted(sheet.cells.items()):
                rows[f"{col_to_letters(col)}{row + 1}"] = cell.raw
            sheets[name] = rows
        return {"version": 1, "sheets": sheets}

    @classmethod
    def from_dict(cls, data: Mapping[str, object], **options) -> "Workbook":
        raw_sheets = data.get("sheets") or {}
        if not isinstance(raw_sheets, Mapping) or not raw_sheets:
            raise ValueError("工作簿里没有工作表")
        wb = cls(list(raw_sheets.keys()), **options)
        for name, cells in raw_sheets.items():
            if not isinstance(cells, Mapping):
                raise ValueError(f"工作表 {name} 的内容格式不对")
            for ref, raw in cells.items():
                try:
                    key = wb.target_key(f"{name}!{ref}")
                except ValueError as exc:
                    raise ValueError(f"工作表 {name} 里的地址 {ref} 无效") from exc
                wb._store_key(key, None if raw is None else str(raw), strict=False)
        wb.recalculate()
        return wb

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, text: str, **options) -> "Workbook":
        return cls.from_dict(json.loads(text), **options)

    # -- CSV --------------------------------------------------------------
    def load_csv(
        self,
        text: str,
        sheet: Optional[str] = None,
        delimiter: str = ",",
    ) -> RecalcStats:
        name = self.resolve_sheet(sheet)
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                if value == "":
                    continue
                self._store_key((name, row_index, col_index), value, strict=False)
        return self.engine.recalculate()

    def to_csv(
        self,
        sheet: Optional[str] = None,
        *,
        formulas: bool = False,
        delimiter: str = ",",
    ) -> str:
        name = self.resolve_sheet(sheet)
        table = self.sheets[name]
        max_row, max_col = table.bounds()
        if max_row < 0:
            return ""
        output = io.StringIO()
        writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
        for row in range(max_row + 1):
            line: List[str] = []
            for col in range(max_col + 1):
                cell = table.cell(row, col)
                if cell is None:
                    line.append("")
                elif formulas:
                    line.append(cell.raw)
                else:
                    line.append(display(self.engine.values.get((name, row, col))))
            writer.writerow(line)
        return output.getvalue()

    def save(self, path: str, *, csv_sheet: Optional[str] = None) -> None:
        suffix = os.path.splitext(path)[1].lower()
        if suffix == ".csv":
            text = self.to_csv(csv_sheet)
        else:
            text = self.to_json()
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    @classmethod
    def load(cls, path: str, **options) -> "Workbook":
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
        if path.lower().endswith(".csv"):
            wb = cls(**options)
            wb.load_csv(text)
            return wb
        return cls.from_json(text, **options)

    # -- 其他 -------------------------------------------------------------
    def copy(self) -> "Workbook":
        clone = Workbook.from_dict(self.to_dict())
        clone.engine.iterative = self.engine.iterative
        clone.engine.max_iterations = self.engine.max_iterations
        clone.engine.tolerance = self.engine.tolerance
        return clone

    def used_bounds(self, sheet: Optional[str] = None) -> Tuple[int, int, int, int]:
        name = self.resolve_sheet(sheet)
        table = self.sheets[name]
        max_row, max_col = table.bounds()
        if max_row < 0:
            return 0, 0, 0, 0
        return 0, 0, max_row, max_col

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Workbook({self.sheet_names!r}, cells={sum(len(s) for s in self.sheets.values())})"


def _parse_literal(raw: str) -> Tuple[Value, str]:
    text = raw.strip()
    if text.startswith("'"):
        # 前导单引号强制文本，和 Excel 的习惯一致
        return raw.lstrip()[1:], "text"
    if text.upper() == "TRUE":
        return True, "bool"
    if text.upper() == "FALSE":
        return False, "bool"
    if _NUMBER_RE.match(text):
        return float(text), "number"
    return raw, "text"
