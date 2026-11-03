"""目标搜索与敏感性分析。

这两件事在表格里很实用，却几乎没人用纯 Python 实现：

* **目标搜索**（Goal Seek）：固定目标格的结果，反解一个输入格。
  本质是一维求根，这里用"从初值向两侧探查 + 二分"。
* **敏感性表**：把一个输入格依次换成候选值，记录目标格的变化。

两者都在保存/恢复的现场上跑，调用者的工作簿不会被弄脏。

求根有两个坑，这里都处理了：

1. 不能一上来就对称扩张区间。像 ``PMT`` 在期数为 0 处是断点，跨过去
   会得到一个"假变号"，二分就会收敛到断点而不是解。所以从初值出发
   一小步一小步往两边探，**哪边先变号就用哪边**。
2. 探查过程中某些点可能根本算不出数（定义域外），这些点直接跳过，
   而不是把区间悄悄移到另一边。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .values import Value, display, is_number
from .workbook import Workbook


@dataclass
class GoalResult:
    target: str
    variable: str
    desired: float
    achieved: Optional[float]
    variable_value: Optional[float]
    iterations: int
    success: bool
    message: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "target": self.target,
            "variable": self.variable,
            "desired": self.desired,
            "achieved": self.achieved,
            "variable_value": self.variable_value,
            "iterations": self.iterations,
            "success": self.success,
            "message": self.message,
        }


def _input_text(number: float) -> str:
    return f"{number:.12g}"


def goal_seek(
    workbook: Workbook,
    target: str,
    variable: str,
    desired: float,
    *,
    low: Optional[float] = None,
    high: Optional[float] = None,
    max_iterations: int = 200,
    tolerance: float = 1e-7,
    apply: bool = False,
) -> GoalResult:
    """反解 ``variable``，使 ``target`` 等于 ``desired``。

    ``apply=False`` 时无论成功与否都会恢复原值，只返回答案；
    ``apply=True`` 时把解写回工作簿（成功的前提下）。
    """
    desired = float(desired)
    tolerance = max(tolerance, 1e-9 * max(1.0, abs(desired)))
    variable_key = workbook.target_key(variable)
    cell = workbook.cell_at(variable_key)
    if cell is not None and cell.kind == "formula":
        raise ValueError("自变量必须是输入格，不能是公式")

    original = workbook.raw(variable)
    current = workbook.get(variable)
    guess = float(current) if is_number(current) else 0.0

    def evaluate_at(point: float) -> float:
        workbook.store(variable, _input_text(point), strict=False)
        workbook.engine.recalculate({variable_key})
        value = workbook.get(target)
        if not is_number(value):
            raise ValueError(f"目标格的结果不是数字：{display(value)}")
        return float(value)

    def residual(point: float) -> float:
        return evaluate_at(point) - desired

    def safe(point: float) -> Optional[float]:
        try:
            return residual(point)
        except (ValueError, ArithmeticError):
            return None

    def finish(success: bool, point: Optional[float], achieved: Optional[float],
               iterations: int, message: str) -> GoalResult:
        _restore(workbook, variable, original, apply=apply and success and point is not None,
                 point=point or 0.0)
        return GoalResult(target, variable, desired, achieved,
                          point if success else None, iterations, success, message)

    f_guess = safe(guess)
    if f_guess is None:
        return finish(False, None, None, 0, "起始值无法求值")
    if abs(f_guess) <= tolerance:
        return finish(True, guess, evaluate_at(guess), 0, "起始值已经是解")

    if low is not None and high is not None:
        lo, hi = sorted((float(low), float(high)))
        f_lo, f_hi = safe(lo), safe(hi)
        if f_lo is None or f_hi is None:
            return finish(False, None, None, 0, "区间端点无法求值")
        if f_lo * f_hi > 0:
            return finish(False, None, None, 0, "区间两端同号，中间没有解")
    else:
        lo = hi = guess
        f_lo = f_hi = f_guess
        step = max(0.1, abs(guess) * 0.1, 1.0)
        found = False
        go_high = True
        # 一次只动一边，动完立刻检查。两边同时扩张会在跨过断点时
        # "同时"变号，而左边那个往往是假根。
        for _ in range(160):
            if go_high:
                hi += step
                probe = safe(hi)
                if probe is None:
                    hi -= step
                else:
                    f_hi = probe
                    if f_hi * f_guess <= 0:
                        found = True
                        break
            else:
                lo -= step
                probe = safe(lo)
                if probe is None:
                    lo += step
                else:
                    f_lo = probe
                    if f_lo * f_guess <= 0:
                        found = True
                        break
                step *= 1.5
            go_high = not go_high
        if not found:
            return finish(False, None, None, 0, "没找到能让目标值变号的区间")

    best_point = guess
    best_error = abs(f_guess)
    for candidate, value in ((lo, f_lo), (hi, f_hi)):
        if value is not None and abs(value) < best_error:
            best_point, best_error = candidate, abs(value)

    iterations = 0
    for iterations in range(1, max_iterations + 1):
        middle = (lo + hi) / 2.0
        f_middle = safe(middle)
        if f_middle is None:
            # 区间中间有定义域空洞，无法继续二分
            break
        if abs(f_middle) < best_error:
            best_point, best_error = middle, abs(f_middle)
        if abs(f_middle) <= tolerance:
            best_point = middle
            break
        if abs(hi - lo) <= 1e-13 * max(1.0, abs(middle)):
            break
        if f_lo is not None and f_lo * f_middle <= 0:
            hi, f_hi = middle, f_middle
        else:
            lo, f_lo = middle, f_middle

    success = best_error <= tolerance
    achieved = evaluate_at(best_point) if success else None
    return finish(success, best_point, achieved, iterations,
                  "" if success else "迭代结束仍未达到容差")


def _restore(
    workbook: Workbook,
    variable: str,
    original: str,
    *,
    apply: bool,
    point: float = 0.0,
) -> None:
    workbook.store(variable, _input_text(point) if apply else original, strict=False)
    workbook.recalculate()


def sensitivity(
    workbook: Workbook,
    target: str,
    variable: str,
    values: Sequence[float],
) -> List[Tuple[float, Value]]:
    """一行式敏感性表：变量取不同值时目标格的结果。"""
    original = workbook.raw(variable)
    rows: List[Tuple[float, Value]] = []
    try:
        for item in values:
            workbook.store(variable, _input_text(float(item)), strict=False)
            workbook.engine.recalculate({workbook.target_key(variable)})
            rows.append((float(item), workbook.get(target)))
    finally:
        workbook.store(variable, original, strict=False)
        workbook.recalculate()
    return rows


def what_if(workbook: Workbook, changes: Dict[str, str]) -> Workbook:
    """在副本上试算，返回一个全新的工作簿。"""
    clone = workbook.copy()
    clone.update(changes)
    clone.recalculate()
    return clone
