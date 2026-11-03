"""内置函数库。

每个函数都遵守同一条约定：参数已经求值（区域是 :class:`RangeValue`），
失败就抛 :class:`FormulaError`，成功就返回一个值。参数个数的检查、
错误值的冒泡由调度器统一处理，函数内部只管业务。

函数按类别注册，``FUNCTIONS`` 同时是 CLI ``functions`` 子命令和网页
函数面板的数据源——文档和实现放在一起，不容易走偏。
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .errors import FormulaError
from .refs import RangeRef
from .values import (
    Err,
    Value,
    compare,
    display,
    equal,
    format_number,
    is_number,
    number_list,
    to_bool,
    to_integer,
    to_number,
    to_text,
)

Number = float


class RangeValue:
    """一块区域在求值时刻的快照，附带几何信息。"""

    __slots__ = ("sheet", "range", "grid")

    def __init__(self, sheet: Optional[str], rng: RangeRef, grid: List[List[Value]]) -> None:
        self.sheet = sheet
        self.range = rng
        self.grid = grid

    @property
    def height(self) -> int:
        return len(self.grid)

    @property
    def width(self) -> int:
        return len(self.grid[0]) if self.grid else 0

    @property
    def size(self) -> int:
        return self.height * self.width

    def flatten(self) -> List[Value]:
        return [value for row in self.grid for value in row]

    def at(self, row: int, col: int) -> Value:
        return self.grid[row][col]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"RangeValue({self.range.a1}, {self.height}x{self.width})"


@dataclass
class Function:
    name: str
    fn: Optional[Callable[[List[Any], Any], Value]]
    min_args: int
    max_args: Optional[int]
    category: str
    summary: str
    params: str = ""
    special: bool = False


FUNCTIONS: Dict[str, Function] = {}


def register(
    name: str,
    min_args: int,
    max_args: Optional[int],
    category: str,
    summary: str,
    params: str = "",
    special: bool = False,
):
    def wrap(fn):
        FUNCTIONS[name] = Function(name, fn, min_args, max_args, category, summary, params, special)
        return fn

    return wrap


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _number(value: Value) -> float:
    return to_number(value)


def _text(value: Value) -> str:
    return to_text(value)


def _integer(value: Value) -> int:
    return to_integer(value)


def _range(value: Any, name: str = "参数") -> RangeValue:
    if not isinstance(value, RangeValue):
        raise FormulaError("#VALUE!", f"{name}必须是区域")
    return value


def _scalar(value: Any, name: str = "参数") -> Value:
    if isinstance(value, RangeValue):
        if value.height == 1 and value.width == 1:
            return value.at(0, 0)
        raise FormulaError("#VALUE!", f"{name}必须是单个值，不能是区域")
    return value


def _numbers(args: Sequence[Any]) -> List[float]:
    return number_list(args)


def _first(numbers: List[float], fallback: float = 0.0) -> float:
    return numbers[0] if numbers else fallback


def _check(value: float, what: str = "结果") -> float:
    if not math.isfinite(value):
        raise FormulaError("#NUM!", f"{what}不是有限数")
    return value


def _div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise FormulaError("#DIV/0!", "除数为零")
    return numerator / denominator


def _safe_pow(base: float, exponent: float) -> float:
    if base == 0 and exponent < 0:
        raise FormulaError("#DIV/0!", "0 不能取负次幂")
    if base < 0 and abs(exponent - round(exponent)) > 1e-12:
        raise FormulaError("#NUM!", "负数不能取分数次幂")
    try:
        result = base ** exponent
    except (OverflowError, ZeroDivisionError):
        raise FormulaError("#NUM!", "幂运算溢出") from None
    if isinstance(result, complex):  # pragma: no cover - 上面已拦截
        raise FormulaError("#NUM!", "结果不是实数")
    return _check(float(result), "幂运算结果")


def _round_half_away(value: float, digits: int = 0) -> float:
    factor = 10.0 ** digits
    scaled = value * factor
    # 加一点点余量，抵消 2.675*100 = 267.49999999999997 这类二进制误差
    rounded = math.floor(abs(scaled) + 0.5 + 1e-9)
    return math.copysign(rounded, scaled) / factor


def _round_toward(value: float, digits: int, mode: str) -> float:
    factor = 10.0 ** digits
    scaled = value * factor
    if mode == "up":
        rounded = math.ceil(abs(scaled) - 1e-12)
    elif mode == "down":
        rounded = math.floor(abs(scaled) + 1e-12)
    else:  # pragma: no cover - 调用点固定
        rounded = round(abs(scaled))
    return math.copysign(rounded, scaled) / factor


def _wildcard(pattern: str):
    pieces = ["^"]
    for ch in pattern:
        if ch == "*":
            pieces.append(".*")
        elif ch == "?":
            pieces.append(".")
        else:
            pieces.append(re.escape(ch))
    pieces.append("$")
    return re.compile("".join(pieces), re.IGNORECASE | re.DOTALL)


def _apply(op: str, left: Any, right: Any) -> bool:
    if op == "=":
        return left == right
    if op == "<>":
        return left != right
    if op == "<":
        return left < right
    if op == ">":
        return left > right
    if op == "<=":
        return left <= right
    return left >= right


def _criterion(criteria: Value):
    """把 COUNTIF 的条件（``">=5"``、``"a*"``、数字）变成谓词。"""
    if isinstance(criteria, Err):
        raise FormulaError(criteria.code, criteria.message)
    if isinstance(criteria, bool):
        return lambda value: isinstance(value, bool) and value == criteria

    if is_number(criteria):
        target = float(criteria)
        return lambda value: (
            is_number(value) and abs(float(value) - target) < 1e-12
        )

    text = "" if criteria is None else str(criteria)
    match = re.match(r"^(<=|>=|<>|=|<|>)(.*)$", text, re.S)
    op, operand = (match.group(1), match.group(2)) if match else ("=", text)

    if op in ("=", "<>") and any(ch in operand for ch in "*?"):
        pattern = _wildcard(operand)
        hit = lambda value: isinstance(value, str) and pattern.match(value) is not None
        return hit if op == "=" else (lambda value: not hit(value))

    number: Optional[float] = None
    body = operand.strip()
    if body:
        try:
            if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", body):
                number = float(body)
        except ValueError:  # pragma: no cover
            number = None

    if number is not None:
        def numeric(value: Value) -> bool:
            if isinstance(value, Err):
                raise FormulaError(value.code, value.message)
            if isinstance(value, bool) or value is None:
                return False
            try:
                current = to_number(value)
            except FormulaError:
                return False
            if op == "=":
                return abs(current - number) < 1e-12
            return _apply(op, current, number)

        return numeric

    def textual(value: Value) -> bool:
        if isinstance(value, Err):
            raise FormulaError(value.code, value.message)
        if value is None:
            current = ""
        elif isinstance(value, str):
            current = value
        else:
            current = display(value)
        return _apply(op, current.casefold(), operand.casefold())

    return textual


# --------------------------------------------------------------------------
# 数学与统计
# --------------------------------------------------------------------------
@register("SUM", 0, None, "数学", "把参数与区域里的数字相加", "SUM(数值1, [数值2], ...)")
def fn_sum(args, ctx):
    return float(sum(_numbers(args)))


@register("PRODUCT", 0, None, "数学", "所有数字的乘积", "PRODUCT(数值1, ...)")
def fn_product(args, ctx):
    numbers = _numbers(args)
    result = 1.0
    for number in numbers:
        result *= number
    return _check(result)


@register("SUMSQ", 0, None, "数学", "平方和", "SUMSQ(数值1, ...)")
def fn_sumsq(args, ctx):
    return float(sum(n * n for n in _numbers(args)))


@register("SUMPRODUCT", 1, None, "数学", "对应位置相乘后求和", "SUMPRODUCT(区域1, [区域2], ...)")
def fn_sumproduct(args, ctx):
    grids = [_range(arg) for arg in args]
    shape = (grids[0].height, grids[0].width)
    if any((grid.height, grid.width) != shape for grid in grids):
        raise FormulaError("#VALUE!", "SUMPRODUCT 的各个区域尺寸必须一致")
    total = 0.0
    for i in range(shape[0]):
        for j in range(shape[1]):
            product = 1.0
            for grid in grids:
                value = grid.at(i, j)
                if isinstance(value, Err):
                    raise FormulaError(value.code, value.message)
                if isinstance(value, bool) or value is None:
                    product *= 1.0 if value is True else 0.0
                elif is_number(value):
                    product *= float(value)
                else:
                    product *= 0.0
            total += product
    return total


@register("ABS", 1, 1, "数学", "绝对值", "ABS(数值)")
def fn_abs(args, ctx):
    return abs(_number(args[0]))


@register("SIGN", 1, 1, "数学", "符号：正数 1、零 0、负数 -1", "SIGN(数值)")
def fn_sign(args, ctx):
    number = _number(args[0])
    return 1.0 if number > 0 else (-1.0 if number < 0 else 0.0)


@register("INT", 1, 1, "数学", "向下取整", "INT(数值)")
def fn_int(args, ctx):
    return float(math.floor(_number(args[0])))


@register("TRUNC", 1, 2, "数学", "向零截断", "TRUNC(数值, [小数位])")
def fn_trunc(args, ctx):
    number = _number(args[0])
    digits = _integer(args[1]) if len(args) > 1 else 0
    return _round_toward(number, digits, "down")


@register("ROUND", 1, 2, "数学", "四舍五入（远离零）", "ROUND(数值, [小数位])")
def fn_round(args, ctx):
    number = _number(args[0])
    digits = _integer(args[1]) if len(args) > 1 else 0
    return _round_half_away(number, digits)


@register("ROUNDUP", 1, 2, "数学", "远离零进位", "ROUNDUP(数值, [小数位])")
def fn_roundup(args, ctx):
    return _round_toward(_number(args[0]), _integer(args[1]) if len(args) > 1 else 0, "up")


@register("ROUNDDOWN", 1, 2, "数学", "向零截断", "ROUNDDOWN(数值, [小数位])")
def fn_rounddown(args, ctx):
    return _round_toward(_number(args[0]), _integer(args[1]) if len(args) > 1 else 0, "down")


@register("MOD", 2, 2, "数学", "取余，符号跟随除数", "MOD(被除数, 除数)")
def fn_mod(args, ctx):
    number, divisor = _number(args[0]), _number(args[1])
    if divisor == 0:
        raise FormulaError("#DIV/0!", "MOD 的除数为零")
    return number - divisor * math.floor(number / divisor)


@register("POWER", 2, 2, "数学", "幂运算", "POWER(底数, 指数)")
def fn_power(args, ctx):
    return _safe_pow(_number(args[0]), _number(args[1]))


@register("SQRT", 1, 1, "数学", "平方根", "SQRT(数值)")
def fn_sqrt(args, ctx):
    number = _number(args[0])
    if number < 0:
        raise FormulaError("#NUM!", "负数没有实数平方根")
    return math.sqrt(number)


@register("EXP", 1, 1, "数学", "自然指数 e^x", "EXP(数值)")
def fn_exp(args, ctx):
    try:
        return math.exp(_number(args[0]))
    except OverflowError:
        raise FormulaError("#NUM!", "指数太大") from None


@register("LN", 1, 1, "数学", "自然对数", "LN(数值)")
def fn_ln(args, ctx):
    number = _number(args[0])
    if number <= 0:
        raise FormulaError("#NUM!", "对数要求正数")
    return math.log(number)


@register("LOG", 1, 2, "数学", "任意底对数（默认 10）", "LOG(数值, [底数])")
def fn_log(args, ctx):
    number = _number(args[0])
    base = _number(args[1]) if len(args) > 1 else 10.0
    if number <= 0 or base <= 0 or base == 1:
        raise FormulaError("#NUM!", "对数的底或真数不合法")
    return math.log(number, base)


@register("LOG10", 1, 1, "数学", "常用对数", "LOG10(数值)")
def fn_log10(args, ctx):
    number = _number(args[0])
    if number <= 0:
        raise FormulaError("#NUM!", "对数要求正数")
    return math.log10(number)


@register("PI", 0, 0, "数学", "圆周率", "PI()")
def fn_pi(args, ctx):
    return math.pi


@register("RAND", 0, 0, "数学", "0 到 1 之间的随机数（易变函数）", "RAND()")
def fn_rand(args, ctx):
    import random

    return random.random()


@register("RANDBETWEEN", 2, 2, "数学", "指定区间内的随机整数（易变函数）", "RANDBETWEEN(下界, 上界)")
def fn_randbetween(args, ctx):
    import random

    low, high = _integer(args[0]), _integer(args[1])
    if low > high:
        raise FormulaError("#NUM!", "下界不能大于上界")
    return float(random.randint(low, high))


@register("GCD", 1, None, "数学", "最大公约数", "GCD(整数1, ...)")
def fn_gcd(args, ctx):
    numbers = [abs(_integer(arg)) for arg in args]
    if any(n < 0 for n in numbers):  # pragma: no cover - abs 之后不会发生
        raise FormulaError("#NUM!", "GCD 只接受非负整数")
    result = 0
    for number in numbers:
        result = math.gcd(result, number)
    return float(result)


@register("LCM", 1, None, "数学", "最小公倍数", "LCM(整数1, ...)")
def fn_lcm(args, ctx):
    numbers = [abs(_integer(arg)) for arg in args]
    result = 1
    for number in numbers:
        if number == 0:
            return 0.0
        result = result * number // math.gcd(result, number)
        if result > 1e15:
            raise FormulaError("#NUM!", "最小公倍数过大")
    return float(result)


@register("FACT", 1, 1, "数学", "阶乘", "FACT(非负整数)")
def fn_fact(args, ctx):
    number = _integer(args[0])
    if number < 0 or number > 170:
        raise FormulaError("#NUM!", "阶乘只接受 0 到 170")
    return float(math.factorial(number))


@register("COMBIN", 2, 2, "数学", "组合数 C(n, k)", "COMBIN(n, k)")
def fn_combin(args, ctx):
    n, k = _integer(args[0]), _integer(args[1])
    if n < 0 or k < 0 or k > n:
        raise FormulaError("#NUM!", "组合数参数不合法")
    return float(math.comb(n, k))


@register("CEILING", 1, 2, "数学", "按倍数向上取整", "CEILING(数值, [倍数])")
def fn_ceiling(args, ctx):
    number = _number(args[0])
    significance = _number(args[1]) if len(args) > 1 else 1.0
    if significance == 0:
        return 0.0
    if number > 0 and significance < 0:
        raise FormulaError("#NUM!", "正数的倍数不能为负")
    return math.ceil(number / significance - 1e-12) * significance


@register("FLOOR", 1, 2, "数学", "按倍数向下取整", "FLOOR(数值, [倍数])")
def fn_floor(args, ctx):
    number = _number(args[0])
    significance = _number(args[1]) if len(args) > 1 else 1.0
    if significance == 0:
        return 0.0
    if number > 0 and significance < 0:
        raise FormulaError("#NUM!", "正数的倍数不能为负")
    return math.floor(number / significance + 1e-12) * significance


@register("MIN", 0, None, "统计", "最小值；没有数字时为 0", "MIN(数值1, ...)")
def fn_min(args, ctx):
    return min(_numbers(args), default=0.0)


@register("MAX", 0, None, "统计", "最大值；没有数字时为 0", "MAX(数值1, ...)")
def fn_max(args, ctx):
    return max(_numbers(args), default=0.0)


@register("AVERAGE", 0, None, "统计", "算术平均", "AVERAGE(数值1, ...)")
def fn_average(args, ctx):
    numbers = _numbers(args)
    if not numbers:
        raise FormulaError("#DIV/0!", "没有可平均的数字")
    return sum(numbers) / len(numbers)


@register("MEDIAN", 0, None, "统计", "中位数", "MEDIAN(数值1, ...)")
def fn_median(args, ctx):
    numbers = sorted(_numbers(args))
    if not numbers:
        raise FormulaError("#NUM!", "没有可计算的中位数")
    middle = len(numbers) // 2
    if len(numbers) % 2:
        return numbers[middle]
    return (numbers[middle - 1] + numbers[middle]) / 2.0


@register("LARGE", 2, 2, "统计", "第 k 大的数", "LARGE(区域, k)")
def fn_large(args, ctx):
    numbers = sorted(_numbers([args[0]]), reverse=True)
    k = _integer(args[1])
    if k < 1 or k > len(numbers):
        raise FormulaError("#NUM!", "k 超出范围")
    return numbers[k - 1]


@register("SMALL", 2, 2, "统计", "第 k 小的数", "SMALL(区域, k)")
def fn_small(args, ctx):
    numbers = sorted(_numbers([args[0]]))
    k = _integer(args[1])
    if k < 1 or k > len(numbers):
        raise FormulaError("#NUM!", "k 超出范围")
    return numbers[k - 1]


@register("STDEV", 0, None, "统计", "样本标准差", "STDEV(数值1, ...)")
def fn_stdev(args, ctx):
    numbers = _numbers(args)
    if len(numbers) < 2:
        raise FormulaError("#DIV/0!", "样本标准差至少需要两个数")
    mean = sum(numbers) / len(numbers)
    variance = sum((x - mean) ** 2 for x in numbers) / (len(numbers) - 1)
    return math.sqrt(variance)


@register("STDEVP", 0, None, "统计", "总体标准差", "STDEVP(数值1, ...)")
def fn_stdevp(args, ctx):
    numbers = _numbers(args)
    if not numbers:
        raise FormulaError("#DIV/0!", "总体标准差至少需要一个数")
    mean = sum(numbers) / len(numbers)
    return math.sqrt(sum((x - mean) ** 2 for x in numbers) / len(numbers))


@register("VAR", 0, None, "统计", "样本方差", "VAR(数值1, ...)")
def fn_var(args, ctx):
    return fn_stdev(args, ctx) ** 2


@register("VARP", 0, None, "统计", "总体方差", "VARP(数值1, ...)")
def fn_varp(args, ctx):
    return fn_stdevp(args, ctx) ** 2


@register("COUNT", 0, None, "统计", "数一数有几个数字", "COUNT(值1, ...)")
def fn_count(args, ctx):
    count = 0
    for arg in args:
        if isinstance(arg, RangeValue):
            for value in arg.flatten():
                if isinstance(value, Err):
                    raise FormulaError(value.code, value.message)
                if is_number(value):
                    count += 1
        elif is_number(arg):
            count += 1
        elif isinstance(arg, str):
            try:
                to_number(arg)
            except FormulaError:
                pass
            else:
                count += 1
    return float(count)


@register("COUNTA", 0, None, "统计", "数一数有几个非空值", "COUNTA(值1, ...)")
def fn_counta(args, ctx):
    count = 0
    for arg in args:
        if isinstance(arg, RangeValue):
            count += sum(1 for value in arg.flatten() if value is not None)
        elif arg is not None:
            count += 1
    return float(count)


@register("COUNTBLANK", 1, 1, "统计", "数一数有几个空格", "COUNTBLANK(区域)")
def fn_countblank(args, ctx):
    rng = _range(args[0])
    count = 0
    for value in rng.flatten():
        if value is None or value == "":
            count += 1
    return float(count)


@register("COUNTIF", 2, 2, "统计", "按条件计数", "COUNTIF(区域, 条件)")
def fn_countif(args, ctx):
    rng = _range(args[0])
    test = _criterion(args[1])
    return float(sum(1 for value in rng.flatten() if test(value)))


@register("SUMIF", 2, 3, "统计", "按条件求和", "SUMIF(条件区域, 条件, [求和区域])")
def fn_sumif(args, ctx):
    rng = _range(args[0])
    test = _criterion(args[1])
    sums = _range(args[2]) if len(args) > 2 and args[2] is not None else rng
    total = 0.0
    for i in range(rng.height):
        for j in range(rng.width):
            if not test(rng.at(i, j)):
                continue
            value = sums.at(i, j) if i < sums.height and j < sums.width else None
            if isinstance(value, Err):
                raise FormulaError(value.code, value.message)
            if is_number(value):
                total += float(value)
    return total


@register("AVERAGEIF", 2, 3, "统计", "按条件求平均", "AVERAGEIF(条件区域, 条件, [平均区域])")
def fn_averageif(args, ctx):
    rng = _range(args[0])
    test = _criterion(args[1])
    sums = _range(args[2]) if len(args) > 2 and args[2] is not None else rng
    total, count = 0.0, 0
    for i in range(rng.height):
        for j in range(rng.width):
            if not test(rng.at(i, j)):
                continue
            value = sums.at(i, j) if i < sums.height and j < sums.width else None
            if isinstance(value, Err):
                raise FormulaError(value.code, value.message)
            if is_number(value):
                total += float(value)
                count += 1
    if not count:
        raise FormulaError("#DIV/0!", "没有满足条件的数字")
    return total / count


# --------------------------------------------------------------------------
# 逻辑
# --------------------------------------------------------------------------
@register("IF", 2, 3, "逻辑", "条件判断", "IF(条件, 真值, [假值])", special=True)
def fn_if(args, ctx):  # pragma: no cover - 由求值器直接分派
    return args[1] if to_bool(args[0]) else (args[2] if len(args) > 2 else False)


@register("IFERROR", 2, 2, "逻辑", "出错时改用另一个值", "IFERROR(值, 出错时的值)", special=True)
def fn_iferror(args, ctx):  # pragma: no cover
    return args[1] if isinstance(args[0], Err) else args[0]


@register("IFNA", 2, 2, "逻辑", "遇到 #N/A 时改用另一个值", "IFNA(值, 出错时的值)", special=True)
def fn_ifna(args, ctx):  # pragma: no cover
    return args[1] if isinstance(args[0], Err) and args[0].code == "#N/A" else args[0]


@register("IFS", 2, None, "逻辑", "多条件分支", "IFS(条件1, 值1, 条件2, 值2, ...)", special=True)
def fn_ifs(args, ctx):  # pragma: no cover
    for i in range(0, len(args) - 1, 2):
        if to_bool(args[i]):
            return args[i + 1]
    raise FormulaError("#N/A", "没有条件成立")


@register("SWITCH", 3, None, "逻辑", "按值匹配分支", "SWITCH(表达式, 值1, 结果1, ..., [默认值])", special=True)
def fn_switch(args, ctx):  # pragma: no cover
    subject = args[0]
    pairs = len(args) - 1
    for i in range(1, len(args) - 1, 2):
        if equal(subject, args[i]):
            return args[i + 1]
    if pairs % 2:
        return args[-1]
    raise FormulaError("#N/A", "没有匹配的分支")


@register("AND", 1, None, "逻辑", "所有条件都为真", "AND(条件1, ...)")
def fn_and(args, ctx):
    values = []
    for arg in args:
        if isinstance(arg, RangeValue):
            for value in arg.flatten():
                if isinstance(value, Err):
                    raise FormulaError(value.code, value.message)
                if isinstance(value, bool):
                    values.append(value)
                elif is_number(value):
                    values.append(value != 0)
        else:
            if arg is None:
                continue
            values.append(to_bool(arg))
    if not values:
        raise FormulaError("#VALUE!", "AND 没有可判断的条件")
    return all(values)


@register("OR", 1, None, "逻辑", "任一条件为真", "OR(条件1, ...)")
def fn_or(args, ctx):
    values = []
    for arg in args:
        if isinstance(arg, RangeValue):
            for value in arg.flatten():
                if isinstance(value, Err):
                    raise FormulaError(value.code, value.message)
                if isinstance(value, bool):
                    values.append(value)
                elif is_number(value):
                    values.append(value != 0)
        else:
            if arg is None:
                continue
            values.append(to_bool(arg))
    if not values:
        raise FormulaError("#VALUE!", "OR 没有可判断的条件")
    return any(values)


@register("XOR", 1, None, "逻辑", "奇数个条件为真", "XOR(条件1, ...)")
def fn_xor(args, ctx):
    values = [to_bool(arg) for arg in args if arg is not None]
    return sum(1 for value in values if value) % 2 == 1


@register("NOT", 1, 1, "逻辑", "取反", "NOT(条件)")
def fn_not(args, ctx):
    return not to_bool(_scalar(args[0]))


@register("TRUE", 0, 0, "逻辑", "逻辑真", "TRUE()")
def fn_true(args, ctx):
    return True


@register("FALSE", 0, 0, "逻辑", "逻辑假", "FALSE()")
def fn_false(args, ctx):
    return False


@register("NA", 0, 0, "逻辑", "产生 #N/A", "NA()")
def fn_na(args, ctx):
    raise FormulaError("#N/A", "由 NA() 产生")


# --------------------------------------------------------------------------
# 文本
# --------------------------------------------------------------------------
@register("LEN", 1, 1, "文本", "文本长度", "LEN(文本)")
def fn_len(args, ctx):
    return float(len(_text(_scalar(args[0]))))


@register("LEFT", 1, 2, "文本", "取左边若干字符", "LEFT(文本, [个数])")
def fn_left(args, ctx):
    text = _text(_scalar(args[0]))
    count = _integer(args[1]) if len(args) > 1 else 1
    if count < 0:
        raise FormulaError("#VALUE!", "字符个数不能为负")
    return text[:count]


@register("RIGHT", 1, 2, "文本", "取右边若干字符", "RIGHT(文本, [个数])")
def fn_right(args, ctx):
    text = _text(_scalar(args[0]))
    count = _integer(args[1]) if len(args) > 1 else 1
    if count < 0:
        raise FormulaError("#VALUE!", "字符个数不能为负")
    return text[-count:] if count else ""


@register("MID", 3, 3, "文本", "从中间截取", "MID(文本, 起始位置, 长度)")
def fn_mid(args, ctx):
    text = _text(_scalar(args[0]))
    start = _integer(args[1])
    length = _integer(args[2])
    if start < 1 or length < 0:
        raise FormulaError("#VALUE!", "起始位置从 1 开始，长度不能为负")
    return text[start - 1:start - 1 + length]


@register("UPPER", 1, 1, "文本", "转大写", "UPPER(文本)")
def fn_upper(args, ctx):
    return _text(_scalar(args[0])).upper()


@register("LOWER", 1, 1, "文本", "转小写", "LOWER(文本)")
def fn_lower(args, ctx):
    return _text(_scalar(args[0])).lower()


@register("PROPER", 1, 1, "文本", "每个单词首字母大写", "PROPER(文本)")
def fn_proper(args, ctx):
    text = _text(_scalar(args[0]))
    out: List[str] = []
    previous_alpha = False
    for ch in text:
        if ch.isalpha():
            out.append(ch.upper() if not previous_alpha else ch.lower())
            previous_alpha = True
        else:
            out.append(ch)
            previous_alpha = False
    return "".join(out)


@register("TRIM", 1, 1, "文本", "去掉首尾空格并压缩连续空格", "TRIM(文本)")
def fn_trim(args, ctx):
    return re.sub(r" +", " ", _text(_scalar(args[0]))).strip(" ")


@register("CONCAT", 0, None, "文本", "拼接所有参数", "CONCAT(值1, ...)")
def fn_concat(args, ctx):
    parts: List[str] = []
    for arg in args:
        if isinstance(arg, RangeValue):
            for value in arg.flatten():
                if isinstance(value, Err):
                    raise FormulaError(value.code, value.message)
                parts.append(display(value))
        else:
            parts.append(_text(arg))
    return "".join(parts)


@register("CONCATENATE", 0, None, "文本", "拼接所有参数（同 CONCAT）", "CONCATENATE(值1, ...)")
def fn_concatenate(args, ctx):
    return fn_concat(args, ctx)


@register("TEXTJOIN", 3, None, "文本", "用分隔符拼接，可跳过空值", "TEXTJOIN(分隔符, 是否跳过空值, 值1, ...)")
def fn_textjoin(args, ctx):
    delimiter = _text(_scalar(args[0]))
    skip_empty = to_bool(_scalar(args[1]))
    parts: List[str] = []
    for arg in args[2:]:
        values = arg.flatten() if isinstance(arg, RangeValue) else [arg]
        for value in values:
            if isinstance(value, Err):
                raise FormulaError(value.code, value.message)
            if value is None or value == "":
                if skip_empty:
                    continue
                parts.append("")
            else:
                parts.append(display(value))
    return delimiter.join(parts)


@register("SUBSTITUTE", 3, 4, "文本", "替换指定的文本", "SUBSTITUTE(文本, 旧, 新, [第几次出现])")
def fn_substitute(args, ctx):
    text = _text(_scalar(args[0]))
    old = _text(_scalar(args[1]))
    new = _text(_scalar(args[2]))
    if not old:
        return text
    if len(args) > 3:
        instance = _integer(args[3])
        if instance < 1:
            raise FormulaError("#VALUE!", "出现次数从 1 开始")
        pieces = text.split(old)
        if instance >= len(pieces):
            return text
        return old.join(pieces[:instance]) + new + old.join(pieces[instance:])
    return text.replace(old, new)


@register("REPLACE", 4, 4, "文本", "按位置替换", "REPLACE(文本, 起始位置, 长度, 新文本)")
def fn_replace(args, ctx):
    text = _text(_scalar(args[0]))
    start = _integer(args[1])
    length = _integer(args[2])
    new = _text(_scalar(args[3]))
    if start < 1 or length < 0:
        raise FormulaError("#VALUE!", "起始位置或长度不合法")
    return text[:start - 1] + new + text[start - 1 + length:]


@register("FIND", 2, 3, "文本", "区分大小写地查找", "FIND(要找的, 文本, [起始位置])")
def fn_find(args, ctx):
    needle = _text(_scalar(args[0]))
    haystack = _text(_scalar(args[1]))
    start = _integer(args[2]) if len(args) > 2 else 1
    if start < 1:
        raise FormulaError("#VALUE!", "起始位置从 1 开始")
    index = haystack.find(needle, start - 1)
    if index < 0:
        raise FormulaError("#VALUE!", f"没有找到“{needle}”")
    return float(index + 1)


@register("SEARCH", 2, 3, "文本", "不区分大小写地查找", "SEARCH(要找的, 文本, [起始位置])")
def fn_search(args, ctx):
    needle = _text(_scalar(args[0])).casefold()
    haystack = _text(_scalar(args[1])).casefold()
    start = _integer(args[2]) if len(args) > 2 else 1
    if start < 1:
        raise FormulaError("#VALUE!", "起始位置从 1 开始")
    index = haystack.find(needle, start - 1)
    if index < 0:
        raise FormulaError("#VALUE!", f"没有找到“{needle}”")
    return float(index + 1)


@register("REPT", 2, 2, "文本", "重复文本", "REPT(文本, 次数)")
def fn_rept(args, ctx):
    text = _text(_scalar(args[0]))
    count = _integer(args[1])
    if count < 0:
        raise FormulaError("#VALUE!", "重复次数不能为负")
    if len(text) * count > 32767:
        raise FormulaError("#VALUE!", "结果超过了单元格长度上限")
    return text * count


@register("VALUE", 1, 1, "文本", "把文本转成数字", "VALUE(文本)")
def fn_value(args, ctx):
    return to_number(_scalar(args[0]))


@register("EXACT", 2, 2, "文本", "区分大小写地比较", "EXACT(文本1, 文本2)")
def fn_exact(args, ctx):
    return _text(_scalar(args[0])) == _text(_scalar(args[1]))


@register("CHAR", 1, 1, "文本", "按编码取字符", "CHAR(编码)")
def fn_char(args, ctx):
    code = _integer(args[0])
    if not 1 <= code <= 255:
        raise FormulaError("#VALUE!", "编码必须在 1 到 255 之间")
    return chr(code)


@register("CODE", 1, 1, "文本", "取首字符的编码", "CODE(文本)")
def fn_code(args, ctx):
    text = _text(_scalar(args[0]))
    if not text:
        raise FormulaError("#VALUE!", "空文本没有编码")
    return float(ord(text[0]))


@register("T", 1, 1, "文本", "是文本就返回，否则返回空串", "T(值)")
def fn_t(args, ctx):
    value = _scalar(args[0])
    if isinstance(value, Err):
        raise FormulaError(value.code, value.message)
    return value if isinstance(value, str) else ""


@register("N", 1, 1, "文本", "转成数字：文本为 0，逻辑值为 1/0", "N(值)")
def fn_n(args, ctx):
    value = _scalar(args[0])
    if isinstance(value, Err):
        raise FormulaError(value.code, value.message)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if is_number(value):
        return float(value)
    return 0.0


def _date_from_serial(serial: float) -> _dt.date:
    days = int(math.floor(serial))
    if days < 1 or days > 2_958_465:
        raise FormulaError("#NUM!", "日期序列号超出范围")
    return _dt.date(1899, 12, 30) + _dt.timedelta(days=days)


def _serial_from_date(value: _dt.date) -> float:
    return float((value - _dt.date(1899, 12, 30)).days)


def _format_date(value: _dt.date, pattern: str) -> str:
    lower = pattern.lower()
    if lower in ("yyyy-mm-dd", "yyyy/m/d"):
        return f"{value.year:04d}-{value.month:02d}-{value.day:02d}" \
            if lower == "yyyy-mm-dd" else f"{value.year}/{value.month}/{value.day}"
    if lower == "yyyy-mm":
        return f"{value.year:04d}-{value.month:02d}"
    if lower == "m/d/yyyy":
        return f"{value.month}/{value.day}/{value.year}"
    return value.isoformat()


@register("TEXT", 2, 2, "文本", "按格式串显示（支持常用数字与日期格式）", "TEXT(值, 格式)")
def fn_text(args, ctx):
    value = _scalar(args[0])
    pattern = _text(_scalar(args[1])).strip()
    if isinstance(value, Err):
        raise FormulaError(value.code, value.message)
    if pattern in ("", "@"):
        return _text(value)

    if any(token in pattern.lower() for token in ("yy", "dd")) and is_number(value):
        return _format_date(_date_from_serial(float(value)), pattern)

    number = to_number(value)
    if pattern in ("0", "#"):
        return f"{number:.0f}"
    if pattern in ("0.0", "#.0"):
        return f"{number:.1f}"
    if pattern in ("0.00", "#.00"):
        return f"{number:.2f}"
    if pattern in ("0.000",):
        return f"{number:.3f}"
    if pattern in ("#,##0", "#,##0.0", "#,##0.00"):
        digits = len(pattern.split(".")[1]) if "." in pattern else 0
        return f"{number:,.{digits}f}"
    if pattern.endswith("%"):
        digits = len(pattern.split(".")[1][:-1]) if "." in pattern else 0
        return f"{number * 100:.{digits}f}%"
    return format_number(number)


# --------------------------------------------------------------------------
# 查找与引用
# --------------------------------------------------------------------------
def _match_equal(left: Value, right: Value) -> bool:
    if isinstance(left, Err) or isinstance(right, Err):
        return False
    if is_number(left) and is_number(right):
        return abs(float(left) - float(right)) < 1e-12
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    if isinstance(left, bool) and isinstance(right, bool):
        return left == right
    return False


@register("VLOOKUP", 3, 4, "查找", "在首列查找并返回同一行某列", "VLOOKUP(查找值, 表格, 列号, [近似匹配])")
def fn_vlookup(args, ctx):
    lookup = _scalar(args[0])
    table = _range(args[1], "表格")
    index = _integer(args[2])
    approximate = to_bool(args[3]) if len(args) > 3 and args[3] is not None else True
    if index < 1:
        raise FormulaError("#VALUE!", "列号从 1 开始")
    if index > table.width:
        raise FormulaError("#REF!", "列号超出了表格宽度")

    found: Optional[int] = None
    for row in range(table.height):
        candidate = table.at(row, 0)
        if _match_equal(candidate, lookup):
            found = row
            break
        if approximate and not isinstance(candidate, Err):
            try:
                if compare(candidate, lookup) <= 0:
                    found = row
            except FormulaError:
                pass
    if found is None:
        raise FormulaError("#N/A", f"表格里没有 {display(lookup)}")
    return table.at(found, index - 1)


@register("HLOOKUP", 3, 4, "查找", "在首行查找并返回同一列某行", "HLOOKUP(查找值, 表格, 行号, [近似匹配])")
def fn_hlookup(args, ctx):
    lookup = _scalar(args[0])
    table = _range(args[1], "表格")
    index = _integer(args[2])
    approximate = to_bool(args[3]) if len(args) > 3 and args[3] is not None else True
    if index < 1:
        raise FormulaError("#VALUE!", "行号从 1 开始")
    if index > table.height:
        raise FormulaError("#REF!", "行号超出了表格高度")

    found: Optional[int] = None
    for col in range(table.width):
        candidate = table.at(0, col)
        if _match_equal(candidate, lookup):
            found = col
            break
        if approximate and not isinstance(candidate, Err):
            try:
                if compare(candidate, lookup) <= 0:
                    found = col
            except FormulaError:
                pass
    if found is None:
        raise FormulaError("#N/A", f"表格里没有 {display(lookup)}")
    return table.at(index - 1, found)


@register("INDEX", 2, 3, "查找", "取区域中第 r 行第 c 列的值", "INDEX(区域, 行号, [列号])")
def fn_index(args, ctx):
    if isinstance(args[0], RangeValue):
        table = args[0]
        if len(args) > 2:
            row, col = _integer(args[1]), _integer(args[2])
        elif table.height == 1:
            # 单行区域只给一个序号时，Excel 把它当作列号
            row, col = 1, _integer(args[1])
        else:
            row, col = _integer(args[1]), 1
        if row < 1 or row > table.height or col < 1 or col > table.width:
            raise FormulaError("#REF!", "INDEX 的位置超出区域")
        return table.at(row - 1, col - 1)
    row = _integer(args[1])
    col = _integer(args[2]) if len(args) > 2 else 1
    if row != 1 or col != 1:
        raise FormulaError("#REF!", "单个值只有第 1 行第 1 列")
    return _scalar(args[0])


@register("MATCH", 2, 3, "查找", "在一行或一列里找位置（从 1 开始）", "MATCH(查找值, 区域, [匹配类型])")
def fn_match(args, ctx):
    lookup = _scalar(args[0])
    rng = _range(args[1], "查找区域")
    if rng.height != 1 and rng.width != 1:
        raise FormulaError("#N/A", "MATCH 只支持一行或一列")
    match_type = _integer(args[2]) if len(args) > 2 else 1
    values = rng.flatten()

    if match_type == 0:
        for index, value in enumerate(values, start=1):
            if _match_equal(value, lookup):
                return float(index)
        raise FormulaError("#N/A", f"没有找到 {display(lookup)}")

    best: Optional[int] = None
    for index, value in enumerate(values, start=1):
        if isinstance(value, Err):
            continue
        try:
            relation = compare(value, lookup)
        except FormulaError:
            continue
        if match_type > 0 and relation <= 0:
            best = index
        elif match_type < 0 and relation >= 0:
            best = index
    if best is None:
        raise FormulaError("#N/A", f"没有找到合适的位置")
    return float(best)


@register("CHOOSE", 2, None, "查找", "按序号从候选中挑一个", "CHOOSE(序号, 候选1, ...)")
def fn_choose(args, ctx):
    index = _integer(args[0])
    if index < 1 or index >= len(args):
        raise FormulaError("#VALUE!", "序号超出候选范围")
    return _scalar(args[index])


@register("ROW", 0, 1, "查找", "当前或指定单元格的行号", "ROW([引用])", special=True)
def fn_row(args, ctx):  # pragma: no cover
    return float(ctx.key[1] + 1)


@register("COLUMN", 0, 1, "查找", "当前或指定单元格的列号", "COLUMN([引用])", special=True)
def fn_column(args, ctx):  # pragma: no cover
    return float(ctx.key[2] + 1)


@register("ROWS", 1, 1, "查找", "区域的行数", "ROWS(区域)", special=True)
def fn_rows(args, ctx):  # pragma: no cover
    return 1.0


@register("COLUMNS", 1, 1, "查找", "区域的列数", "COLUMNS(区域)", special=True)
def fn_columns(args, ctx):  # pragma: no cover
    return 1.0


# --------------------------------------------------------------------------
# 日期
# --------------------------------------------------------------------------
@register("DATE", 3, 3, "日期", "由年月日得到序列号", "DATE(年, 月, 日)")
def fn_date(args, ctx):
    year, month, day = _integer(args[0]), _integer(args[1]), _integer(args[2])
    if not 1900 <= year <= 9999:
        raise FormulaError("#NUM!", "年份必须在 1900 到 9999 之间")
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    try:
        base = _dt.date(year, month, 1) + _dt.timedelta(days=day - 1)
    except (ValueError, OverflowError):
        raise FormulaError("#NUM!", "日期超出范围") from None
    return _serial_from_date(base)


@register("YEAR", 1, 1, "日期", "取年份", "YEAR(序列号)")
def fn_year(args, ctx):
    return float(_date_from_serial(_number(args[0])).year)


@register("MONTH", 1, 1, "日期", "取月份", "MONTH(序列号)")
def fn_month(args, ctx):
    return float(_date_from_serial(_number(args[0])).month)


@register("DAY", 1, 1, "日期", "取日", "DAY(序列号)")
def fn_day(args, ctx):
    return float(_date_from_serial(_number(args[0])).day)


@register("WEEKDAY", 1, 2, "日期", "星期几", "WEEKDAY(序列号, [类型])")
def fn_weekday(args, ctx):
    date = _date_from_serial(_number(args[0]))
    kind = _integer(args[1]) if len(args) > 1 else 1
    iso = date.isoweekday()  # 周一 = 1
    if kind == 1:
        return float(iso % 7 + 1)   # 周日 = 1
    if kind == 2:
        return float(iso)
    if kind == 3:
        return float(iso - 1)       # 周一 = 0
    raise FormulaError("#NUM!", "WEEKDAY 的类型只支持 1、2、3")


@register("EDATE", 2, 2, "日期", "若干个月后的同一天", "EDATE(序列号, 月数)")
def fn_edate(args, ctx):
    date = _date_from_serial(_number(args[0]))
    months = _integer(args[1])
    total = date.year * 12 + (date.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    if not 1900 <= year <= 9999:
        raise FormulaError("#NUM!", "结果超出日期范围")
    day = min(date.day, _days_in_month(year, month))
    return _serial_from_date(_dt.date(year, month, day))


@register("EOMONTH", 2, 2, "日期", "若干个月后那个月的最后一天", "EOMONTH(序列号, 月数)")
def fn_eomonth(args, ctx):
    date = _date_from_serial(_number(args[0]))
    months = _integer(args[1])
    total = date.year * 12 + (date.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    if not 1900 <= year <= 9999:
        raise FormulaError("#NUM!", "结果超出日期范围")
    return _serial_from_date(_dt.date(year, month, _days_in_month(year, month)))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (_dt.date(year, month + 1, 1) - _dt.date(year, month, 1)).days


@register("DAYS", 2, 2, "日期", "两个日期相差的天数", "DAYS(结束, 开始)")
def fn_days(args, ctx):
    return _number(args[0]) - _number(args[1])


@register("TODAY", 0, 0, "日期", "今天的序列号（易变函数）", "TODAY()")
def fn_today(args, ctx):
    return _serial_from_date(_dt.date.today())


@register("NOW", 0, 0, "日期", "当前日期时间（易变函数）", "NOW()")
def fn_now(args, ctx):
    now = _dt.datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fraction = (now - midnight).total_seconds() / 86400.0
    return _serial_from_date(now.date()) + fraction


@register("DATEVALUE", 1, 1, "日期", "把日期文本转成序列号", "DATEVALUE(文本)")
def fn_datevalue(args, ctx):
    text = _text(_scalar(args[0])).strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return _serial_from_date(_dt.datetime.strptime(text, pattern).date())
        except ValueError:
            continue
    raise FormulaError("#VALUE!", f"“{text}”不是可识别的日期")


# --------------------------------------------------------------------------
# 财务
# --------------------------------------------------------------------------
def _financial_args(args: Sequence[Any]) -> Tuple[float, float, float, float, float]:
    rate = _number(args[0])
    nper = _number(args[1])
    pv = _number(args[2])
    rest = args[3:]
    fv = _number(rest[0]) if len(rest) > 0 and rest[0] is not None else 0.0
    when = _number(rest[1]) if len(rest) > 1 and rest[1] is not None else 0.0
    if when not in (0.0, 1.0):
        raise FormulaError("#NUM!", "付款时点只能是 0（期末）或 1（期初）")
    return rate, nper, pv, fv, when


def _pmt(rate: float, nper: float, pv: float, fv: float, when: float) -> float:
    if nper == 0:
        raise FormulaError("#NUM!", "期数不能为零")
    if rate == 0:
        return -(pv + fv) / nper
    factor = (1 + rate) ** nper
    if factor == 1:
        return -(pv + fv) / nper
    return -(rate * (pv * factor + fv)) / ((1 + rate * when) * (factor - 1))


@register("PMT", 3, 5, "财务", "等额本息每期还款额", "PMT(利率, 期数, 现值, [终值], [时点])")
def fn_pmt(args, ctx):
    return _pmt(*_financial_args(args))


@register("FV", 3, 5, "财务", "等额付款后的终值", "FV(利率, 期数, 每期付款, [现值], [时点])")
def fn_fv(args, ctx):
    rate, nper, pmt, pv, when = _financial_args(args)
    if rate == 0:
        return -(pv + pmt * nper)
    factor = (1 + rate) ** nper
    return -(pv * factor + pmt * (1 + rate * when) * (factor - 1) / rate)


@register("PV", 3, 5, "财务", "一系列未来付款的现值", "PV(利率, 期数, 每期付款, [终值], [时点])")
def fn_pv(args, ctx):
    rate, nper, pmt, fv, when = _financial_args(args)
    if rate == 0:
        return -(fv + pmt * nper)
    factor = (1 + rate) ** nper
    return -(fv + pmt * (1 + rate * when) * (factor - 1) / rate) / factor


@register("NPV", 2, None, "财务", "按折现率计算净现值", "NPV(折现率, 现金流1, ...)")
def fn_npv(args, ctx):
    rate = _number(args[0])
    if rate <= -1:
        raise FormulaError("#NUM!", "折现率必须大于 -100%")
    flows = _numbers(args[1:])
    return sum(flow / (1 + rate) ** (index + 1) for index, flow in enumerate(flows))


@register("IRR", 1, 2, "财务", "使净现值为零的折现率", "IRR(现金流区域, [猜测值])")
def fn_irr(args, ctx):
    flows = _numbers([args[0]])
    if len(flows) < 2:
        raise FormulaError("#NUM!", "IRR 至少需要两笔现金流")
    if not (any(flow > 0 for flow in flows) and any(flow < 0 for flow in flows)):
        raise FormulaError("#NUM!", "现金流必须同时包含正负")

    def npv(rate: float) -> float:
        return sum(flow / (1 + rate) ** index for index, flow in enumerate(flows))

    low, high = -0.9999, 10.0
    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        raise FormulaError("#NUM!", "在 -100% 到 1000% 之间找不到根")
    for _ in range(300):
        middle = (low + high) / 2
        f_middle = npv(middle)
        if abs(f_middle) < 1e-10:
            return middle
        if f_low * f_middle <= 0:
            high, f_high = middle, f_middle
        else:
            low, f_low = middle, f_middle
    return (low + high) / 2


@register("SLN", 3, 3, "财务", "直线折旧每期金额", "SLN(原值, 残值, 期数)")
def fn_sln(args, ctx):
    cost, salvage, life = _number(args[0]), _number(args[1]), _number(args[2])
    if life == 0:
        raise FormulaError("#DIV/0!", "折旧期数不能为零")
    return (cost - salvage) / life


# --------------------------------------------------------------------------
# 信息
# --------------------------------------------------------------------------
@register("ISBLANK", 1, 1, "信息", "是否为空单元格", "ISBLANK(值)")
def fn_isblank(args, ctx):
    return _scalar(args[0]) is None


@register("ISNUMBER", 1, 1, "信息", "是否为数字", "ISNUMBER(值)")
def fn_isnumber(args, ctx):
    return is_number(_scalar(args[0]))


@register("ISTEXT", 1, 1, "信息", "是否为文本", "ISTEXT(值)")
def fn_istext(args, ctx):
    return isinstance(_scalar(args[0]), str)


@register("ISLOGICAL", 1, 1, "信息", "是否为逻辑值", "ISLOGICAL(值)")
def fn_islogical(args, ctx):
    return isinstance(_scalar(args[0]), bool)


@register("ISERROR", 1, 1, "信息", "是否为任意错误", "ISERROR(值)", special=True)
def fn_iserror(args, ctx):
    return isinstance(_scalar(args[0]), Err)


@register("ISERR", 1, 1, "信息", "是否为除 #N/A 之外的错误", "ISERR(值)", special=True)
def fn_iserr(args, ctx):
    value = _scalar(args[0])
    return isinstance(value, Err) and value.code != "#N/A"


@register("ISNA", 1, 1, "信息", "是否为 #N/A", "ISNA(值)", special=True)
def fn_isna(args, ctx):
    value = _scalar(args[0])
    return isinstance(value, Err) and value.code == "#N/A"


@register("ISEVEN", 1, 1, "信息", "是否为偶数", "ISEVEN(数值)")
def fn_iseven(args, ctx):
    return _integer(args[0]) % 2 == 0


@register("ISODD", 1, 1, "信息", "是否为奇数", "ISODD(数值)")
def fn_isodd(args, ctx):
    return _integer(args[0]) % 2 != 0


#: 需要在求值器里特殊处理（惰性求值或依赖引用几何）的函数
SPECIAL_FORMS = {name for name, spec in FUNCTIONS.items() if spec.special}

#: 每次重算都必须重新求值的函数
VOLATILE = {"RAND", "RANDBETWEEN", "NOW", "TODAY"}


def call_function(name: str, args: List[Any], ctx) -> Value:
    spec = FUNCTIONS.get(name)
    if spec is None:
        raise FormulaError("#NAME?", f"没有名为 {name} 的函数")
    if len(args) < spec.min_args:
        raise FormulaError("#VALUE!", f"{name} 至少需要 {spec.min_args} 个参数")
    if spec.max_args is not None and len(args) > spec.max_args:
        raise FormulaError("#VALUE!", f"{name} 最多接受 {spec.max_args} 个参数")
    assert spec.fn is not None
    return spec.fn(args, ctx)


def catalogue() -> List[Function]:
    return sorted(FUNCTIONS.values(), key=lambda item: (item.category, item.name))
