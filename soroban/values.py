"""值模型与类型转换。

一个单元格的值只有五种可能：

===========  ==================================================
``None``     空
``float``    数字（布尔单独处理，避免 ``True == 1`` 的坑）
``str``      文本
``bool``     逻辑值
``Err``      错误值
===========  ==================================================

Excel 的隐式转换出了名地多，这里把规则集中到 ``to_number`` /
``to_text`` / ``to_bool`` 三个函数里，其余模块一律不许自己猜。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, List, Optional, Union

from .errors import FormulaError

#: 类型别名，仅用于标注
Value = Union[None, float, str, bool, "Err"]

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?$")


class Err:
    """一个错误值。相等性只看错误码，消息只用于展示。"""

    __slots__ = ("code", "message")

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Err) and other.code == self.code

    def __hash__(self) -> int:
        return hash(("Err", self.code))

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"Err({self.code!r})"


def is_error(value: Any) -> bool:
    return isinstance(value, Err)


def is_number(value: Any) -> bool:
    """注意 ``bool`` 是 ``int`` 的子类，这里必须排除。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def format_number(number: float, digits: int = 10) -> str:
    """把数字变成人看的字符串：整数不带小数点，浮点不留二进制尾巴。"""
    if isinstance(number, int):
        return str(number)
    if math.isnan(number) or math.isinf(number):
        return "#NUM!"
    if number == 0:
        return "0"
    if abs(number) < 1e15 and number == int(number):
        return str(int(number))
    text = f"{number:.{digits}g}"
    if "e" in text or "E" in text:
        mantissa, _, exponent = text.lower().partition("e")
        text = f"{mantissa}e{int(exponent):+d}"
    return text


def display(value: Value) -> str:
    """面向界面 / CSV 的展示形式。"""
    if value is None:
        return ""
    if isinstance(value, Err):
        return value.code
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return format_number(value)
    if isinstance(value, int):
        return str(value)
    return str(value)


def to_number(value: Value) -> float:
    """按 Excel 的规则转数字；失败抛 ``#VALUE!``。"""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise FormulaError("#NUM!", "结果不是有限数")
        return number
    if value is None:
        return 0.0
    if isinstance(value, Err):
        raise FormulaError(value.code, value.message)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise FormulaError("#VALUE!", "空文本不能参与计算")
        if not _NUMBER_RE.match(text):
            raise FormulaError("#VALUE!", f"“{value}”不是数字")
        percent = text.endswith("%")
        body = text[:-1].strip() if percent else text
        try:
            number = float(body)
        except ValueError:  # pragma: no cover - 正则已经挡住了
            raise FormulaError("#VALUE!", f"“{value}”不是数字") from None
        return number / 100.0 if percent else number
    raise FormulaError("#VALUE!", f"无法把 {type(value).__name__} 当成数字")


def to_integer(value: Value) -> int:
    number = to_number(value)
    if number != int(number):
        # Excel 在取整参数上会截断，这里保持一致
        return int(number)
    return int(number)


def to_text(value: Value) -> str:
    if value is None:
        return ""
    if isinstance(value, Err):
        raise FormulaError(value.code, value.message)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return format_number(float(value))
    return str(value)


def to_bool(value: Value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, Err):
        raise FormulaError(value.code, value.message)
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().upper()
        if text == "TRUE":
            return True
        if text == "FALSE":
            return False
        raise FormulaError("#VALUE!", f"“{value}”不是逻辑值")
    raise FormulaError("#VALUE!", "无法转成逻辑值")


def _rank(value: Value) -> int:
    if isinstance(value, bool):
        return 2
    if isinstance(value, str):
        return 1
    return 0


def compare(left: Value, right: Value) -> int:
    """返回 -1 / 0 / 1，遵循 Excel 的跨类型顺序：数字 < 文本 < 逻辑值。"""
    for value in (left, right):
        if isinstance(value, Err):
            raise FormulaError(value.code, value.message)

    if left is None and right is None:
        return 0
    if left is None:
        left = _blank_like(right)
    if right is None:
        right = _blank_like(left)

    left_rank, right_rank = _rank(left), _rank(right)
    if left_rank != right_rank:
        return -1 if left_rank < right_rank else 1
    if isinstance(left, str) and isinstance(right, str):
        left, right = left.casefold(), right.casefold()
    if left == right:  # type: ignore[operator]
        return 0
    return -1 if left < right else 1  # type: ignore[operator]


def _blank_like(other: Value) -> Value:
    """空单元格与谁比较，就变成谁那一类的“零值”。"""
    if isinstance(other, bool):
        return False
    if isinstance(other, str):
        return ""
    return 0.0


def equal(left: Value, right: Value) -> bool:
    return compare(left, right) == 0


def flatten(args: Iterable[Any]) -> List[Value]:
    """把可能含 RangeValue 的参数摊平成一串标量。"""
    from .functions import RangeValue

    out: List[Value] = []
    for arg in args:
        if isinstance(arg, RangeValue):
            out.extend(arg.flatten())
        else:
            out.append(arg)
    return out


def number_list(args: Iterable[Any], *, direct_bool: bool = True) -> List[float]:
    """收集数字，规则与 SUM 一致：

    * 区域里的文本、逻辑值、空格一律跳过
    * 直接写出来的参数会尝试转换（``SUM("3")`` = 3）
    * 任何一个错误值都会向上冒泡
    """
    from .functions import RangeValue

    numbers: List[float] = []
    for arg in args:
        if isinstance(arg, RangeValue):
            for value in arg.flatten():
                if isinstance(value, Err):
                    raise FormulaError(value.code, value.message)
                if isinstance(value, bool) or value is None:
                    continue
                if isinstance(value, (int, float)):
                    numbers.append(float(value))
        else:
            if isinstance(arg, Err):
                raise FormulaError(arg.code, arg.message)
            if arg is None:
                continue
            if isinstance(arg, bool) and not direct_bool:
                numbers.append(1.0 if arg else 0.0)
                continue
            numbers.append(to_number(arg))
    return numbers


def optional(value: Optional[Value], default: Any) -> Any:
    return default if value is None else value
