"""Soroban —— 零依赖的电子表格计算引擎。

>>> from soroban import Workbook
>>> wb = Workbook()
>>> wb.update({"A1": "10", "A2": "20", "A3": "=SUM(A1:A2)"})
>>> wb.get("A3")
30.0
"""

from __future__ import annotations

from typing import Optional

from .engine import Key, RecalcStats
from .errors import ERROR_CODES, FormulaError
from .functions import FUNCTIONS, Function, RangeValue, catalogue
from .parser import Node, formula_of, parse_formula, unparse
from .refs import CellRef, RangeRef
from .solver import GoalResult, goal_seek, sensitivity, what_if
from .values import Err, Value, display, format_number
from .workbook import Cell, Sheet, Workbook

__version__ = "1.0.0"
__all__ = [
    "Workbook",
    "Sheet",
    "Cell",
    "Value",
    "Err",
    "FormulaError",
    "ERROR_CODES",
    "CellRef",
    "RangeRef",
    "RangeValue",
    "Function",
    "FUNCTIONS",
    "RecalcStats",
    "GoalResult",
    "goal_seek",
    "sensitivity",
    "what_if",
    "parse_formula",
    "unparse",
    "formula_of",
    "display",
    "format_number",
    "evaluate",
    "__version__",
]


def evaluate(expression: str, workbook: Optional[Workbook] = None) -> Value:
    """在临时工作簿里算一个表达式，方便脚本里做一次性计算。"""
    book = workbook or Workbook()
    book.set("A1", expression if expression.startswith("=") else "=" + expression)
    return book.get("A1")
