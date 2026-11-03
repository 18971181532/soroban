"""公式词法分析。

公式语言比编程语言小得多，但有两个坑：

1. ``LOG10`` 既像函数名又像单元格（LOG 列第 10 行），只能靠后面有没有
   左括号来区分——这一点交给语法分析器处理。
2. 工作表名可能带空格，得用单引号包起来，且内部的单引号要写成两个。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from .errors import parse_error

_NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_IDENT = re.compile(r"[A-Za-z_$\u4e00-\u9fff][A-Za-z0-9_$.\u4e00-\u9fff]*")

_OPERATORS = ("<=", ">=", "<>", "+", "-", "*", "/", "^", "&", "=", "<", ">")

#: 词法单元的种类
NUM = "num"
STR = "str"
QNAME = "qname"
IDENT = "ident"
OP = "op"
LPAREN = "("
RPAREN = ")"
COMMA = ","
COLON = ":"
BANG = "!"
PERCENT = "%"
EOF = "eof"

PUNCTUATION = {
    "(": LPAREN,
    ")": RPAREN,
    ",": COMMA,
    ":": COLON,
    "!": BANG,
    "%": PERCENT,
}


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    pos: int

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Token({self.kind}, {self.text!r})"


def tokenize(formula: str) -> List[Token]:
    tokens: List[Token] = []
    i, length = 0, len(formula)

    while i < length:
        ch = formula[i]

        if ch in " \t\r\n":
            i += 1
            continue

        if ch == '"':
            value, i = _read_string(formula, i, '"')
            tokens.append(Token(STR, value, i))
            continue

        if ch == "'":
            value, i = _read_string(formula, i, "'")
            tokens.append(Token(QNAME, value, i))
            continue

        if ch.isdigit() or (ch == "." and i + 1 < length and formula[i + 1].isdigit()):
            match = _NUMBER.match(formula, i)
            assert match is not None
            tokens.append(Token(NUM, match.group(0), i))
            i = match.end()
            continue

        if ch.isalpha() or ch in "_$" or "\u4e00" <= ch <= "\u9fff":
            match = _IDENT.match(formula, i)
            assert match is not None
            tokens.append(Token(IDENT, match.group(0), i))
            i = match.end()
            continue

        if ch in PUNCTUATION:
            tokens.append(Token(PUNCTUATION[ch], ch, i))
            i += 1
            continue

        for op in _OPERATORS:
            if formula.startswith(op, i):
                tokens.append(Token(OP, op, i))
                i += len(op)
                break
        else:
            raise parse_error(f"无法识别的字符 {ch!r}", i)

    tokens.append(Token(EOF, "", length))
    return tokens


def _read_string(formula: str, start: int, quote: str):
    """读取引号串；连写两个引号表示引号本身。"""
    i = start + 1
    parts: List[str] = []
    while i < len(formula):
        ch = formula[i]
        if ch == quote:
            if i + 1 < len(formula) and formula[i + 1] == quote:
                parts.append(quote)
                i += 2
                continue
            return "".join(parts), i + 1
        parts.append(ch)
        i += 1
    raise parse_error("引号没有闭合", start)


def describe_tokens(tokens: List[Token]) -> str:  # pragma: no cover - 调试辅助
    return " ".join(f"{tok.kind}:{tok.text}" for tok in tokens if tok.kind != EOF)
