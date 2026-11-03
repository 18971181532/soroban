"""测试辅助：在固定工作表的角落算一个表达式，避免和数据区打架。"""

from __future__ import annotations

from typing import Dict, Optional

from soroban import Workbook
from soroban.values import Err

SCRATCH = "AE1"


def book_with(cells: Optional[Dict[str, object]] = None) -> Workbook:
    workbook = Workbook()
    if cells:
        workbook.update({key: str(value) for key, value in cells.items()})
    return workbook


def calc(expression: str, cells: Optional[Dict[str, object]] = None):
    workbook = book_with(cells)
    workbook.set(SCRATCH, expression)
    return workbook.get(SCRATCH)


def calc_error(expression: str, cells: Optional[Dict[str, object]] = None) -> Err:
    value = calc(expression, cells)
    assert isinstance(value, Err), f"{expression} 期望错误值，实际是 {value!r}"
    return value


def numeric(value, places: int = 10):
    return round(float(value), places)
