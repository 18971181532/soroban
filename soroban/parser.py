"""递归下降语法分析。

优先级从低到高：

``比较 < 连接(&) < 加减 < 乘除 < 幂 < 一元正负 < 百分号 < 原子``

有几处刻意与 Excel 对齐：

* ``-2^2`` 等于 4（一元负号先结合），而不是数学课本里的 -4
* ``^`` 右结合：``2^3^2`` = 2^9
* 比较运算左结合，可以写 ``1<2<3``（结果会被转成逻辑值再比较）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple, Union

from . import lexer
from .errors import FormulaError, parse_error
from .lexer import Token
from .refs import CellRef, RangeRef, parse_cell, quote_sheet
from .values import format_number

COMPARISONS = ("=", "<>", "<", ">", "<=", ">=")


# --------------------------------------------------------------------------
# 抽象语法树
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class NumberNode:
    value: float


@dataclass(frozen=True)
class TextNode:
    value: str


@dataclass(frozen=True)
class BoolNode:
    value: bool


@dataclass(frozen=True)
class BlankNode:
    pass


@dataclass(frozen=True)
class RefNode:
    ref: CellRef


@dataclass(frozen=True)
class RangeNode:
    range: RangeRef


@dataclass(frozen=True)
class UnaryNode:
    op: str
    operand: "Node"


@dataclass(frozen=True)
class BinaryNode:
    op: str
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class CallNode:
    name: str
    args: Tuple["Node", ...]


Node = Union[
    NumberNode, TextNode, BoolNode, BlankNode, RefNode, RangeNode,
    UnaryNode, BinaryNode, CallNode,
]


# --------------------------------------------------------------------------
# 语法分析
# --------------------------------------------------------------------------
class Parser:
    def __init__(self, tokens: List[Token], text: str = "") -> None:
        self.tokens = tokens
        self.text = text
        self.index = 0

    # -- 基础操作 ---------------------------------------------------------
    def peek(self, offset: int = 0) -> Token:
        index = min(self.index + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def advance(self) -> Token:
        token = self.tokens[self.index]
        if token.kind != lexer.EOF:
            self.index += 1
        return token

    def expect(self, kind: str, what: str) -> Token:
        token = self.peek()
        if token.kind != kind:
            found = token.text or "公式结尾"
            raise parse_error(f"这里需要{what}，却遇到 {found!r}", token.pos)
        return self.advance()

    # -- 优先级 -----------------------------------------------------------
    def parse(self) -> Node:
        node = self.comparison()
        if self.peek().kind != lexer.EOF:
            token = self.peek()
            raise parse_error(f"多余的内容 {token.text!r}", token.pos)
        return node

    def comparison(self) -> Node:
        node = self.concat()
        while self.peek().kind == lexer.OP and self.peek().text in COMPARISONS:
            op = self.advance().text
            node = BinaryNode(op, node, self.concat())
        return node

    def concat(self) -> Node:
        node = self.additive()
        while self.peek().kind == lexer.OP and self.peek().text == "&":
            self.advance()
            node = BinaryNode("&", node, self.additive())
        return node

    def additive(self) -> Node:
        node = self.multiplicative()
        while self.peek().kind == lexer.OP and self.peek().text in ("+", "-"):
            op = self.advance().text
            node = BinaryNode(op, node, self.multiplicative())
        return node

    def multiplicative(self) -> Node:
        node = self.power()
        while self.peek().kind == lexer.OP and self.peek().text in ("*", "/"):
            op = self.advance().text
            node = BinaryNode(op, node, self.power())
        return node

    def power(self) -> Node:
        node = self.unary()
        if self.peek().kind == lexer.OP and self.peek().text == "^":
            self.advance()
            # 右结合：右边继续用 power
            return BinaryNode("^", node, self.power())
        return node

    def unary(self) -> Node:
        token = self.peek()
        if token.kind == lexer.OP and token.text in ("+", "-"):
            self.advance()
            return UnaryNode(token.text, self.unary())
        return self.postfix()

    def postfix(self) -> Node:
        node = self.primary()
        while self.peek().kind == lexer.PERCENT:
            self.advance()
            node = UnaryNode("%", node)
        return node

    def primary(self) -> Node:
        token = self.peek()

        if token.kind == lexer.NUM:
            self.advance()
            return NumberNode(float(token.text))

        if token.kind == lexer.STR:
            self.advance()
            return TextNode(token.text)

        if token.kind == lexer.QNAME:
            if self.peek(1).kind != lexer.BANG:
                raise parse_error("单引号只能用来引用工作表名", token.pos)
            self.advance()
            self.advance()
            return self.sheet_reference(token.text, token.pos)

        if token.kind == lexer.IDENT:
            # 函数调用优先：LOG10(...) 是函数，不是 LOG 列第 10 行
            if self.peek(1).kind == lexer.LPAREN:
                return self.call()

            if self.peek(1).kind == lexer.BANG:
                self.advance()
                self.advance()
                return self.sheet_reference(token.text, token.pos)

            upper = token.text.upper()
            if upper in ("TRUE", "FALSE"):
                self.advance()
                return BoolNode(upper == "TRUE")

            ref = parse_cell(token.text)
            if ref is not None:
                self.advance()
                return self.maybe_range(RefNode(ref))

            raise parse_error(f"不认识的名称 {token.text!r}", token.pos)

        if token.kind == lexer.LPAREN:
            self.advance()
            node = self.comparison()
            self.expect(lexer.RPAREN, "右括号")
            return node

        raise parse_error("表达式还没写完", token.pos)

    def sheet_reference(self, sheet: str, position: int) -> Node:
        token = self.peek()
        if token.kind != lexer.IDENT:
            raise parse_error("工作表名后面要跟单元格引用", token.pos)
        ref = parse_cell(token.text, sheet)
        if ref is None:
            raise parse_error(f"{token.text!r} 不是单元格引用", token.pos)
        self.advance()
        return self.maybe_range(RefNode(ref))

    def maybe_range(self, node: Node) -> Node:
        if self.peek().kind != lexer.COLON:
            return node
        assert isinstance(node, RefNode)
        colon = self.advance()
        token = self.peek()
        if token.kind != lexer.IDENT:
            raise parse_error("冒号右边要跟单元格引用", colon.pos)
        end = parse_cell(token.text)
        if end is None:
            raise parse_error(f"{token.text!r} 不是单元格引用", token.pos)
        if end.sheet and node.ref.sheet and end.sheet != node.ref.sheet:
            raise parse_error("区域的两端不在同一张工作表", token.pos)
        self.advance()
        start = node.ref
        return RangeNode(
            RangeRef(start.sheet or end.sheet, start.row, start.col, end.row, end.col)
        )

    def call(self) -> Node:
        name = self.advance().text.upper()
        self.expect(lexer.LPAREN, "左括号")
        args: List[Node] = []
        if self.peek().kind != lexer.RPAREN:
            while True:
                if self.peek().kind in (lexer.COMMA, lexer.RPAREN):
                    args.append(BlankNode())
                else:
                    args.append(self.comparison())
                if self.peek().kind == lexer.COMMA:
                    self.advance()
                    continue
                break
        self.expect(lexer.RPAREN, "右括号")
        return CallNode(name, tuple(args))


def parse_formula(text: str) -> Node:
    """把 ``=SUM(A1:A3)*2`` 变成语法树。"""
    source = text.strip()
    if source.startswith("="):
        source = source[1:]
    if not source.strip():
        raise parse_error("公式是空的")
    return Parser(lexer.tokenize(source), source).parse()


# --------------------------------------------------------------------------
# 遍历与还原
# --------------------------------------------------------------------------
def walk(node: Node) -> Iterator[Node]:
    yield node
    if isinstance(node, UnaryNode):
        yield from walk(node.operand)
    elif isinstance(node, BinaryNode):
        yield from walk(node.left)
        yield from walk(node.right)
    elif isinstance(node, CallNode):
        for arg in node.args:
            yield from walk(arg)


def references(node: Node) -> Tuple[List[CellRef], List[RangeRef]]:
    """收集公式里出现的单元格与区域（不去重，保持出现顺序）。"""
    cells: List[CellRef] = []
    ranges: List[RangeRef] = []
    for item in walk(node):
        if isinstance(item, RefNode):
            cells.append(item.ref)
        elif isinstance(item, RangeNode):
            ranges.append(item.range)
    return cells, ranges


def function_names(node: Node) -> List[str]:
    return [item.name for item in walk(node) if isinstance(item, CallNode)]


_PRECEDENCE = {
    "=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1,
    "&": 2,
    "+": 3, "-": 3,
    "*": 4, "/": 4,
    "^": 5,
}


def unparse(node: Node) -> str:
    """语法树还原成规范公式文本（不含前导等号）。"""
    if isinstance(node, NumberNode):
        return format_number(node.value)
    if isinstance(node, TextNode):
        return '"' + node.value.replace('"', '""') + '"'
    if isinstance(node, BoolNode):
        return "TRUE" if node.value else "FALSE"
    if isinstance(node, BlankNode):
        return ""
    if isinstance(node, RefNode):
        ref = node.ref
        sheet = f"{quote_sheet(ref.sheet)}!" if ref.sheet else ""
        return sheet + ref.absolute
    if isinstance(node, RangeNode):
        rng = node.range
        sheet = f"{quote_sheet(rng.sheet)}!" if rng.sheet else ""
        return sheet + rng.a1
    if isinstance(node, UnaryNode):
        if node.op == "%":
            return f"{unparse(node.operand)}%"
        return f"{node.op}{unparse(node.operand)}"
    if isinstance(node, BinaryNode):
        left = unparse(node.left)
        right = unparse(node.right)
        if isinstance(node.left, BinaryNode) and \
                _PRECEDENCE[node.left.op] < _PRECEDENCE[node.op]:
            left = f"({left})"
        if isinstance(node.right, BinaryNode) and \
                _PRECEDENCE[node.right.op] <= _PRECEDENCE[node.op]:
            right = f"({right})"
        return f"{left}{node.op}{right}"
    if isinstance(node, CallNode):
        return f"{node.name}({','.join(unparse(arg) for arg in node.args)})"
    raise TypeError(f"未知节点：{node!r}")  # pragma: no cover


def formula_of(node: Node) -> str:
    return "=" + unparse(node)
