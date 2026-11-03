"""计算引擎：依赖图、求值与重算调度。

整体思路
--------

1. 每个公式在建立时解析一次，抽出它引用的单元格与区域，登记成有向边
   ``公式 -> 它依赖的东西``。
2. 重算时先从"被改动的格子"出发，沿反向边收集所有受影响公式。
3. 在受影响子图上跑 Tarjan，按强连通分量输出顺序求值——Tarjan 弹出的
   顺序天然是"被依赖者先出"，正好就是求值顺序。
4. 大小大于 1 的分量（或自环）就是循环引用。默认整组标 ``#CYCLE!``；
   打开迭代计算后，按 Gauss-Seidel 方式反复代入，直到收敛或达到上限。

这样"全量重算"和"改一个格子"走的是同一条代码路径，前者只是根集合更大，
不会出现两套语义。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from .errors import FormulaError
from .functions import (
    FUNCTIONS,
    SPECIAL_FORMS,
    VOLATILE,
    RangeValue,
    call_function,
)
from .parser import (
    BinaryNode,
    BlankNode,
    BoolNode,
    CallNode,
    Node,
    NumberNode,
    RangeNode,
    RefNode,
    TextNode,
    UnaryNode,
    function_names,
    references,
    unparse,
)
from .refs import CellRef, RangeRef, quote_sheet
from .values import Err, Value, compare, display, is_number, to_bool, to_number, to_text

Key = Tuple[str, int, int]


@dataclass
class RecalcStats:
    """一次重算的统计，用于界面提示与基准测试。"""

    computed: int = 0
    cycles: int = 0
    iterations: int = 0
    converged: bool = True
    seconds: float = 0.0
    full: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "computed": self.computed,
            "cycles": self.cycles,
            "iterations": self.iterations,
            "converged": self.converged,
            "seconds": round(self.seconds, 6),
            "full": self.full,
        }


class Context:
    """求值上下文：当前单元格、当前工作表、取值入口。"""

    __slots__ = ("engine", "sheet", "key")

    def __init__(self, engine: "Engine", sheet: str, key: Key) -> None:
        self.engine = engine
        self.sheet = sheet
        self.key = key

    def value_of(self, ref: CellRef) -> Value:
        sheet = ref.sheet or self.sheet
        if sheet not in self.engine.workbook.sheets:
            raise FormulaError("#REF!", f"工作表“{sheet}”不存在")
        return self.engine.values.get((sheet, ref.row, ref.col))

    def range_of(self, rng: RangeRef) -> RangeValue:
        sheet = rng.sheet or self.sheet
        if sheet not in self.engine.workbook.sheets:
            raise FormulaError("#REF!", f"工作表“{sheet}”不存在")

        row2, col2 = rng.row2, rng.col2
        # 整列引用（A1:A1048576）不该真的铺开一百万个格子。把右下角
        # 裁到已用区域即可：多出来的部分全是空值，对求和 / 计数没有影响。
        if rng.size > 50_000:
            max_row, max_col = self.engine.workbook.sheets[sheet].bounds()
            row2 = min(row2, max(max_row, rng.row1))
            col2 = min(col2, max(max_col, rng.col1))

        grid = [
            [
                self.engine.values.get((sheet, row, col))
                for col in range(rng.col1, col2 + 1)
            ]
            for row in range(rng.row1, row2 + 1)
        ]
        return RangeValue(sheet, rng, grid)


class Engine:
    def __init__(self, workbook) -> None:
        self.workbook = workbook
        self.values: Dict[Key, Value] = {}
        self.asts: Dict[Key, Node] = {}
        self.bad: Dict[Key, str] = {}

        # 正向：公式 -> 依赖
        self.prec_cells: Dict[Key, Set[Key]] = {}
        self.prec_ranges: Dict[Key, List[RangeRef]] = {}

        # 反向：被引用者 -> 公式
        self.rev_cells: Dict[Key, Set[Key]] = {}
        self.rev_ranges: Dict[Tuple[str, int], List[Tuple[RangeRef, Key]]] = {}

        self.volatile: Set[Key] = set()
        self.iterative = False
        self.max_iterations = 100
        self.tolerance = 1e-9
        self.stats = RecalcStats()

    # -- 登记与撤销 -------------------------------------------------------
    def attach(self, key: Key, cell) -> None:
        self.detach(key)
        if cell.kind == "formula" and cell.ast is not None:
            self.asts[key] = cell.ast
            cells, ranges = references(cell.ast)
            precedents = {ref.key(key[0]) for ref in cells}
            # 单元格引用可能写在工作表里，key() 已经带上了默认表名
            self.prec_cells[key] = precedents
            self.prec_ranges[key] = list(ranges)
            for precedent in precedents:
                self.rev_cells.setdefault(precedent, set()).add(key)
            for rng in ranges:
                sheet = rng.sheet or key[0]
                for col in range(rng.col1, rng.col2 + 1):
                    self.rev_ranges.setdefault((sheet, col), []).append((rng, key))
            if any(name in VOLATILE for name in function_names(cell.ast)):
                self.volatile.add(key)
        elif cell.kind == "bad":
            self.bad[key] = cell.message
            self.values[key] = Err("#PARSE!", cell.message)
        else:
            self.values[key] = cell.value

    def detach(self, key: Key) -> None:
        for precedent in self.prec_cells.pop(key, ()):  # 正向边
            bucket = self.rev_cells.get(precedent)
            if bucket is not None:
                bucket.discard(key)
                if not bucket:
                    self.rev_cells.pop(precedent, None)
        for rng in self.prec_ranges.pop(key, ()):
            sheet = rng.sheet or key[0]
            for col in range(rng.col1, rng.col2 + 1):
                bucket = self.rev_ranges.get((sheet, col))
                if not bucket:
                    continue
                bucket[:] = [item for item in bucket if item[1] != key]
                if not bucket:
                    self.rev_ranges.pop((sheet, col), None)
        self.asts.pop(key, None)
        self.bad.pop(key, None)
        self.volatile.discard(key)
        self.values.pop(key, None)

    # -- 查询 -------------------------------------------------------------
    def dependents_of(self, key: Key) -> Set[Key]:
        found = set(self.rev_cells.get(key, ()))
        sheet, row, col = key
        for rng, dependent in self.rev_ranges.get((sheet, col), ()):
            if rng.contains(row, col):
                found.add(dependent)
        return found

    def precedent_keys(self, key: Key) -> Set[Key]:
        """公式依赖的**实际存在**的单元格（区域会展开）。"""
        found = set(self.prec_cells.get(key, ()))
        for rng in self.prec_ranges.get(key, ()):
            found.update(self._cells_in(rng, key[0]))
        return found

    def _cells_in(self, rng: RangeRef, default_sheet: str) -> Iterator[Key]:
        sheet = rng.sheet or default_sheet
        if rng.size <= 20_000:
            for row, col in rng.cells():
                yield (sheet, row, col)
            return
        # 超大区域（整列）不要逐格遍历，改为扫已登记的公式
        for (sheet_name, row, col) in self.asts:
            if sheet_name == sheet and rng.contains(row, col):
                yield (sheet_name, row, col)

    def contributors(self, key: Key) -> Set[Key]:
        """传递闭包：这个格子最终由哪些格子算出来。"""
        seen: Set[Key] = set()
        stack = [key]
        while stack:
            current = stack.pop()
            for precedent in self.precedent_keys(current):
                if precedent not in seen:
                    seen.add(precedent)
                    stack.append(precedent)
        seen.discard(key)
        return seen

    def precedents_of(self, key: Key) -> Tuple[List[Key], List[RangeRef]]:
        cells = sorted(self.prec_cells.get(key, ()))
        ranges = list(self.prec_ranges.get(key, ()))
        return cells, ranges

    # -- 重算 -------------------------------------------------------------
    def recalculate(self, roots: Optional[Iterable[Key]] = None) -> RecalcStats:
        start = time.perf_counter()
        full = roots is None
        if full:
            base = set(self.asts) | set(self.bad) | set(self.volatile)
        else:
            base = set(roots) | set(self.volatile)

        affected = self._expand(base)
        components = self._components(affected)
        stats = RecalcStats(full=full)

        for component in components:
            cyclic = len(component) > 1 or component[0] in self._adjacency(component[0], affected)
            if cyclic:
                stats.cycles += 1
                if self.iterative:
                    iterations, converged = self._iterate(component, stats)
                    stats.iterations = max(stats.iterations, iterations)
                    stats.converged = stats.converged and converged
                    stats.computed += len(component)
                else:
                    for key in component:
                        self.values[key] = Err("#CYCLE!", "循环引用：" + self._ref_name(key))
                    stats.computed += len(component)
            else:
                key = component[0]
                self.values[key] = self._evaluate(key)
                stats.computed += 1

        stats.seconds = time.perf_counter() - start
        self.stats = stats
        return stats

    def _expand(self, roots: Set[Key]) -> Set[Key]:
        seen = set(roots)
        stack = list(roots)
        while stack:
            key = stack.pop()
            for dependent in self.dependents_of(key):
                if dependent not in seen:
                    seen.add(dependent)
                    stack.append(dependent)
        return {key for key in seen if key in self.asts or key in self.bad}

    def _adjacency(self, key: Key, scope: Set[Key]) -> Set[Key]:
        """只保留落在受影响集合里的前驱，已算好的不用再排队。"""
        return {p for p in self.precedent_keys(key) if p in scope}

    def _components(self, nodes: Set[Key]) -> List[List[Key]]:
        """迭代版 Tarjan；输出顺序即"被依赖者先出"。"""
        adjacency = {key: sorted(self._adjacency(key, nodes)) for key in nodes}
        index: Dict[Key, int] = {}
        low: Dict[Key, int] = {}
        on_stack: Set[Key] = set()
        stack: List[Key] = []
        result: List[List[Key]] = []
        counter = 0

        for root in sorted(nodes):
            if root in index:
                continue
            index[root] = low[root] = counter
            counter += 1
            stack.append(root)
            on_stack.add(root)
            work: List[Tuple[Key, Iterator[Key]]] = [(root, iter(adjacency[root]))]

            while work:
                node, iterator = work[-1]
                advanced = False
                for neighbour in iterator:
                    if neighbour not in index:
                        index[neighbour] = low[neighbour] = counter
                        counter += 1
                        stack.append(neighbour)
                        on_stack.add(neighbour)
                        work.append((neighbour, iter(adjacency[neighbour])))
                        advanced = True
                        break
                    if neighbour in on_stack:
                        low[node] = min(low[node], index[neighbour])
                if advanced:
                    continue

                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component: List[Key] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    result.append(sorted(component))
        return result

    def _evaluate(self, key: Key) -> Value:
        ast = self.asts.get(key)
        if ast is None:
            if key in self.bad:
                return Err("#PARSE!", self.bad[key])
            return self.values.get(key)
        try:
            value = evaluate(ast, Context(self, key[0], key))
        except FormulaError as exc:
            return Err(exc.code, exc.message)
        except RecursionError:  # pragma: no cover - 公式极少嵌套到这种程度
            return Err("#NUM!", "公式嵌套过深")
        if isinstance(value, RangeValue):
            if value.size == 1:
                return value.at(0, 0)
            return Err("#VALUE!", "公式结果不能是一个区域")
        return value

    def _iterate(self, component: List[Key], stats: RecalcStats) -> Tuple[int, bool]:
        converged = False
        iterations = 0
        for iteration in range(1, self.max_iterations + 1):
            iterations = iteration
            delta = 0.0
            for key in component:
                new = self._evaluate(key)
                old = self.values.get(key)
                if is_number(new) and is_number(old):
                    delta = max(delta, abs(float(new) - float(old)))
                elif new != old:
                    delta = max(delta, 1.0)
                self.values[key] = new
            if delta <= self.tolerance:
                converged = True
                break
        return iterations, converged

    def _ref_name(self, key: Key) -> str:
        from .refs import col_to_letters

        return f"{key[0]}!{col_to_letters(key[2])}{key[1] + 1}"

    # -- 审计 -------------------------------------------------------------
    def audit(self, key: Key, depth: int = 2, limit: int = 24) -> Dict[str, object]:
        def visit(current: Key, level: int, path: Set[Key]) -> Dict[str, object]:
            cell = self.workbook.cell_at(current)
            node: Dict[str, object] = {
                "ref": f"{current[0]}!{_a1(current)}",
                "sheet": current[0],
                "cell": _a1(current),
                "formula": cell.raw if cell is not None and cell.kind == "formula" else None,
                "kind": cell.kind if cell is not None else "blank",
                "value": display(self.values.get(current)),
            }
            if level >= depth or current in path:
                return node
            children: List[Dict[str, object]] = []
            for precedent in sorted(self.precedent_keys(current))[:limit]:
                children.append(visit(precedent, level + 1, path | {current}))
            if children:
                node["precedents"] = children
            if len(children) == limit:
                node["truncated"] = True
            return node

        root = visit(key, 0, set())
        root["dependents"] = [
            f"{item[0]}!{_a1(item)}" for item in sorted(self.dependents_of(key))
        ]
        return root

    def explain(self, key: Key, depth: int = 3) -> str:
        cell = self.workbook.cell_at(key)
        if cell is None:
            return f"{self._ref_name(key)} 是空单元格"
        lines = [f"{self._ref_name(key)}  {cell.raw}"]
        lines.append(f"  = {display(self.values.get(key))}")
        if cell.kind == "formula":
            cells, ranges = self.precedents_of(key)
            for precedent in cells:
                lines.append(
                    f"  ← {self._ref_name(precedent)} = {display(self.values.get(precedent))}"
                )
            for rng in ranges:
                if rng.size <= 8:
                    sheet = rng.sheet or key[0]
                    for row, col in rng.cells():
                        item = (sheet, row, col)
                        lines.append(f"  ← {self._ref_name(item)} = "
                                     f"{display(self.values.get(item))}")
                else:
                    lines.append(f"  ← 区域 {rng.a1}（{rng.size} 格）")
        dependents = sorted(self.dependents_of(key))
        if dependents:
            lines.append("  被这些格子引用：")
            for dependent in dependents[:12]:
                lines.append(f"  → {self._ref_name(dependent)}")
            if len(dependents) > 12:
                lines.append(f"  → …… 另有 {len(dependents) - 12} 个")
        return "\n".join(lines)


def _a1(key: Key) -> str:
    from .refs import col_to_letters

    return f"{col_to_letters(key[2])}{key[1] + 1}"


# --------------------------------------------------------------------------
# 表达式求值
# --------------------------------------------------------------------------
def evaluate(node: Node, ctx: Context) -> Value:
    if isinstance(node, NumberNode):
        return node.value
    if isinstance(node, TextNode):
        return node.value
    if isinstance(node, BoolNode):
        return node.value
    if isinstance(node, BlankNode):
        return None
    if isinstance(node, RefNode):
        return ctx.value_of(node.ref)
    if isinstance(node, RangeNode):
        return ctx.range_of(node.range)

    if isinstance(node, UnaryNode):
        value = evaluate(node.operand, ctx)
        number = to_number(value if not isinstance(value, RangeValue) else _single(value))
        if node.op == "-":
            return -number
        if node.op == "+":
            return number
        return number / 100.0

    if isinstance(node, BinaryNode):
        return _binary(node, ctx)

    if isinstance(node, CallNode):
        return _call(node, ctx)

    raise TypeError(f"未知节点 {node!r}")  # pragma: no cover


def _single(value: RangeValue) -> Value:
    if value.size != 1:
        raise FormulaError("#VALUE!", "这里需要单个值，不能是区域")
    return value.at(0, 0)


def _binary(node: BinaryNode, ctx: Context) -> Value:
    left = evaluate(node.left, ctx)
    right = evaluate(node.right, ctx)
    if isinstance(left, RangeValue):
        left = _single(left)
    if isinstance(right, RangeValue):
        right = _single(right)

    if node.op == "&":
        return to_text(left) + to_text(right)

    if node.op in ("=", "<>", "<", ">", "<=", ">="):
        relation = compare(left, right)
        return {
            "=": relation == 0,
            "<>": relation != 0,
            "<": relation < 0,
            ">": relation > 0,
            "<=": relation <= 0,
            ">=": relation >= 0,
        }[node.op]

    a, b = to_number(left), to_number(right)
    if node.op == "+":
        return a + b
    if node.op == "-":
        return a - b
    if node.op == "*":
        return a * b
    if node.op == "/":
        if b == 0:
            raise FormulaError("#DIV/0!", "除数为零")
        return a / b
    if node.op == "^":
        if a == 0 and b < 0:
            raise FormulaError("#DIV/0!", "0 不能取负次幂")
        if a < 0 and abs(b - round(b)) > 1e-12:
            raise FormulaError("#NUM!", "负数不能取分数次幂")
        try:
            result = a ** b
        except (OverflowError, ZeroDivisionError):
            raise FormulaError("#NUM!", "幂运算溢出") from None
        if not isinstance(result, float):
            result = float(result)
        return result
    raise FormulaError("#VALUE!", f"未知运算符 {node.op}")  # pragma: no cover


def _call(node: CallNode, ctx: Context) -> Value:
    name = node.name
    if name in SPECIAL_FORMS:
        return _special(name, node.args, ctx)

    args: List[Value] = []
    for arg in node.args:
        value = evaluate(arg, ctx)
        if isinstance(value, RangeValue) and value.size != 1:
            args.append(value)
        elif isinstance(value, RangeValue):
            args.append(value.at(0, 0))
        else:
            args.append(value)
    return call_function(name, args, ctx)


def _lazy(node: Node, ctx: Context) -> Value:
    """求值但把异常转成错误值，供 IFERROR / IFNA 使用。"""
    try:
        value = evaluate(node, ctx)
    except FormulaError as exc:
        return Err(exc.code, exc.message)
    return _single(value) if isinstance(value, RangeValue) else value


def _truth(node: Node, ctx: Context) -> bool:
    value = evaluate(node, ctx)
    if isinstance(value, RangeValue):
        value = _single(value)
    return to_bool(value)


def _ref_node(node: Node) -> Optional[CellRef]:
    if isinstance(node, RefNode):
        return node.ref
    return None


def _range_node(node: Node) -> Optional[RangeRef]:
    if isinstance(node, RangeNode):
        return node.range
    if isinstance(node, RefNode):
        ref = node.ref
        return RangeRef(ref.sheet, ref.row, ref.col, ref.row, ref.col)
    return None


def _special(name: str, args: Sequence[Node], ctx: Context) -> Value:
    if name == "IF":
        if not 2 <= len(args) <= 3:
            raise FormulaError("#VALUE!", "IF 需要 2 或 3 个参数")
        if _truth(args[0], ctx):
            return evaluate(args[1], ctx)
        return evaluate(args[2], ctx) if len(args) > 2 else False

    if name in ("IFERROR", "IFNA"):
        if len(args) != 2:
            raise FormulaError("#VALUE!", f"{name} 需要 2 个参数")
        value = _lazy(args[0], ctx)
        if isinstance(value, Err) and (name == "IFERROR" or value.code == "#N/A"):
            return evaluate(args[1], ctx)
        return value

    if name in ("ISERROR", "ISERR", "ISNA"):
        if len(args) != 1:
            raise FormulaError("#VALUE!", f"{name} 需要 1 个参数")
        value = _lazy(args[0], ctx)
        if name == "ISNA":
            return isinstance(value, Err) and value.code == "#N/A"
        if name == "ISERR":
            return isinstance(value, Err) and value.code != "#N/A"
        return isinstance(value, Err)

    if name == "IFS":
        if len(args) < 2 or len(args) % 2:
            raise FormulaError("#VALUE!", "IFS 需要成对的条件与结果")
        for index in range(0, len(args), 2):
            if _truth(args[index], ctx):
                return evaluate(args[index + 1], ctx)
        raise FormulaError("#N/A", "没有条件成立")

    if name == "SWITCH":
        if len(args) < 3:
            raise FormulaError("#VALUE!", "SWITCH 至少需要 3 个参数")
        subject = evaluate(args[0], ctx)
        if isinstance(subject, RangeValue):
            subject = _single(subject)
        pairs = len(args) - 1
        for index in range(1, len(args) - 1, 2):
            candidate = evaluate(args[index], ctx)
            if isinstance(candidate, RangeValue):
                candidate = _single(candidate)
            if compare(subject, candidate) == 0:
                return evaluate(args[index + 1], ctx)
        if pairs % 2:
            return evaluate(args[-1], ctx)
        raise FormulaError("#N/A", "没有匹配的分支")

    if name in ("ROW", "COLUMN"):
        if len(args) > 1:
            raise FormulaError("#VALUE!", f"{name} 最多一个参数")
        if not args:
            row, col = ctx.key[1], ctx.key[2]
        else:
            ref = _ref_node(args[0])
            rng = _range_node(args[0])
            if ref is not None:
                row, col = ref.row, ref.col
            elif rng is not None:
                row, col = rng.row1, rng.col1
            else:
                raise FormulaError("#VALUE!", f"{name} 的参数必须是引用")
        return float(row + 1 if name == "ROW" else col + 1)

    if name in ("ROWS", "COLUMNS"):
        if len(args) != 1:
            raise FormulaError("#VALUE!", f"{name} 需要 1 个参数")
        rng = _range_node(args[0])
        if rng is None:
            raise FormulaError("#VALUE!", f"{name} 的参数必须是区域")
        return float(rng.height if name == "ROWS" else rng.width)

    raise FormulaError("#NAME?", f"没有名为 {name} 的函数")  # pragma: no cover
