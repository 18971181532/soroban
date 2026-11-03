"""命令行入口。

设计原则：每个子命令都能单独完成一件事，输出可以直接贴进 README。
表格用列号 + 行号标出位置，和网页里的坐标一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional, Sequence

from . import __version__
from .console import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    YELLOW,
    enable_utf8,
    pad,
    paint,
    rule,
    table,
)
from .demo import BUDGET, LOAN, build_demo, headline
from .errors import FormulaError
from .functions import catalogue
from .refs import col_to_letters
from .solver import goal_seek, sensitivity
from .values import Err, Value, display, is_number
from .workbook import Workbook


# --------------------------------------------------------------------------
# 载入与展示
# --------------------------------------------------------------------------
def load_workbook(path: str, sheet: Optional[str] = None) -> Workbook:
    try:
        return Workbook.load(path)
    except FileNotFoundError:
        raise SystemExit(f"找不到文件：{path}") from None
    except (ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"读不了这个文件：{exc}") from None


def _looks_numeric(text: str) -> bool:
    try:
        float(text.replace(",", ""))
    except ValueError:
        return False
    return True


def grid_lines(
    workbook: Workbook,
    sheet: Optional[str] = None,
    *,
    max_rows: int = 40,
    formulas: bool = False,
) -> str:
    name = workbook.resolve_sheet(sheet)
    source = workbook.sheets[name]
    max_row, max_col = source.bounds()
    if max_row < 0:
        return "（空表）"
    max_row = min(max_row, max_rows - 1)
    max_col = min(max_col, 25)

    headers = [""] + [col_to_letters(col) for col in range(max_col + 1)]
    rows: List[List[str]] = []
    for row in range(max_row + 1):
        line = [str(row + 1)]
        for col in range(max_col + 1):
            cell = source.cell(row, col)
            if cell is None:
                line.append("")
            elif formulas:
                line.append(cell.raw)
            else:
                line.append(display(workbook.engine.values.get((name, row, col))))
        rows.append(line)

    aligns = ["right"]
    for col in range(max_col + 1):
        values = [row[col + 1] for row in rows if row[col + 1] != ""]
        aligns.append("right" if values and all(_looks_numeric(v) for v in values) else "left")

    text = table(headers, rows, aligns, max_width=22)
    if source.bounds()[0] > max_rows - 1:
        text += "\n" + paint(f"  …… 只显示了前 {max_rows} 行", DIM)
    return text


def cells_payload(workbook: Workbook, sheet: Optional[str] = None) -> Dict[str, object]:
    name = workbook.resolve_sheet(sheet)
    source = workbook.sheets[name]
    cells = []
    for (row, col), cell in sorted(source.cells.items()):
        value = workbook.engine.values.get((name, row, col))
        cells.append({
            "ref": f"{col_to_letters(col)}{row + 1}",
            "raw": cell.raw,
            "display": display(value),
            "kind": cell.kind,
            "error": value.code if isinstance(value, Err) else None,
        })
    return {"sheet": name, "cells": cells, "stats": workbook.stats.as_dict()}


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_calc(args) -> int:
    failures = 0
    for expression in args.expressions:
        try:
            value = _calc(expression)
        except FormulaError as exc:
            failures += 1
            print(f"{expression}  {paint('✗', RED)}  {paint(exc.code, RED)} {exc.message}")
            continue
        if isinstance(value, Err):
            failures += 1
            print(f"{expression}  →  {paint(value.code, RED)}  {value.message}")
        else:
            print(f"{expression}  →  {paint(display(value), GREEN)}")
    return 1 if failures else 0


def _calc(expression: str) -> Value:
    from . import evaluate

    return evaluate(expression)


def cmd_run(args) -> int:
    workbook = load_workbook(args.file)
    if args.json:
        payload = cells_payload(workbook, args.sheet)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    name = workbook.resolve_sheet(args.sheet)
    print(paint(f"{name}", BOLD) + paint(f"  ·  {args.file}", DIM))
    print(grid_lines(workbook, args.sheet, max_rows=args.rows, formulas=args.formulas))
    stats = workbook.stats
    print(paint(
        f"  {stats.computed} 个公式，{stats.seconds * 1000:.1f} ms"
        + (f"，{stats.cycles} 处循环引用" if stats.cycles else ""),
        DIM,
    ))
    if args.csv:
        print()
        print(workbook.to_csv(args.sheet))
    return 0


def cmd_audit(args) -> int:
    workbook = load_workbook(args.file)
    print(paint(workbook.explain(args.cell), BOLD))
    print()
    tree = workbook.audit(args.cell, depth=args.depth)

    def render(node: Dict[str, object], prefix: str, connector: str, last: bool) -> None:
        head = str(node.get("ref", ""))
        raw = node.get("formula") or ""
        value = node.get("value", "")
        if raw:
            head += f"  {paint(str(raw), CYAN)}"
        if value != "":
            head += f"  {paint('= ' + str(value), DIM)}"
        print(prefix + connector + head)
        children = node.get("precedents") or []
        if not isinstance(children, list):
            return
        child_prefix = prefix + (("   " if last else "│  ") if connector else "")
        for index, child in enumerate(children):
            render(child, child_prefix,
                   "└─ " if index == len(children) - 1 else "├─ ",
                   index == len(children) - 1)

    render(tree, "", "", True)
    if tree.get("truncated"):
        print(paint("  …… 依赖太多，只展开了前 24 个", DIM))
    return 0


def cmd_goal(args) -> int:
    workbook = load_workbook(args.file)
    try:
        result = goal_seek(
            workbook, args.target, args.variable, args.value,
            low=args.low, high=args.high, apply=bool(args.save),
        )
    except ValueError as exc:
        print(paint(f"无法求解：{exc}", RED))
        return 1

    if not result.success:
        print(paint(f"没有解：{result.message}", RED))
        return 1

    print(paint("目标搜索", BOLD))
    print(f"  目标格    {args.target}  =  {display(result.achieved)}")
    print(f"  自变量    {args.variable}  =  {display(result.variable_value)}")
    print(f"  迭代次数  {result.iterations}")

    if args.save:
        workbook.save(args.save)
        print(paint(f"  已写入 {args.save}", GREEN))
    return 0


def cmd_sensitivity(args) -> int:
    try:
        values = [float(item) for item in args.values.split(",") if item.strip()]
    except ValueError:
        print(paint("--values 需要逗号分隔的数字", RED))
        return 1
    if not values:
        print(paint("--values 不能为空", RED))
        return 1

    workbook = load_workbook(args.file)
    rows = sensitivity(workbook, args.target, args.variable, values)
    body = [[display(item), display(value)] for item, value in rows]
    print(paint("敏感性分析", BOLD)
          + paint(f"   {args.variable} → {args.target}", DIM))
    print(table([args.variable, args.target], body, ["right", "right"]))
    return 0


def cmd_functions(args) -> int:
    keyword = (args.filter or "").casefold()
    current = None
    count = 0
    for spec in catalogue():
        haystack = f"{spec.name} {spec.summary} {spec.params}".casefold()
        if keyword and keyword not in haystack:
            continue
        if spec.category != current:
            if current is not None:
                print()
            current = spec.category
            print(paint(current, BOLD))
        print(f"  {paint(pad(spec.name, 12), CYAN)} {spec.summary}")
        print(f"  {'':<12} {paint(spec.params, DIM)}")
        count += 1
    if not count:
        print("没有匹配的函数")
        return 1
    print()
    print(paint(f"共 {count} 个函数", DIM))
    return 0


def cmd_demo(args) -> int:
    workbook = build_demo()
    print(paint("Soroban 示例工作簿", BOLD) + paint(f"   v{__version__}", DIM))
    print(rule(64))
    for item in headline(workbook):
        print("  " + item)
    print()
    print(paint("经营表", BOLD) + paint("   （=公式，其余是输入）", DIM))
    print(grid_lines(workbook, BUDGET, max_rows=16))
    print()

    print(paint("依赖审计", BOLD) + paint("   经营!D8 的毛利从哪来", DIM))
    print(workbook.explain(f"{BUDGET}!D8"))
    print()

    print(paint("目标搜索", BOLD) + paint("   想让月供降到 6000，需要多少年？", DIM))
    result = goal_seek(workbook, f"{LOAN}!B6", f"{LOAN}!B4", 6000)
    if result.success:
        print(f"  贷款!B4 = {display(result.variable_value)} 年"
              f"（月供 {display(result.achieved)}，{result.iterations} 次迭代）")
    else:
        print("  " + result.message)
    print()

    print(paint("利率敏感性", BOLD) + paint("   利率变化对月供的影响", DIM))
    rows = sensitivity(workbook, f"{LOAN}!B6", f"{LOAN}!B3",
                       [0.035, 0.042, 0.049, 0.056, 0.063])
    body = [[f"{rate * 100:.1f}%", display(value)] for rate, value in rows]
    print(table(["年利率", "月供"], body, ["right", "right"]))
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    workbook = load_workbook(args.file) if args.file else build_demo()
    serve(workbook, port=args.port, open_browser=not args.no_open)
    return 0


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soroban",
        description="零依赖的电子表格计算引擎：公式、依赖图、增量重算与审计。",
    )
    parser.add_argument("--version", action="version", version=f"soroban {__version__}")
    sub = parser.add_subparsers(dest="command")

    calc = sub.add_parser("calc", help="直接计算表达式")
    calc.add_argument("expressions", nargs="+")
    calc.set_defaults(func=cmd_calc)

    run = sub.add_parser("run", help="计算表格文件并打印")
    run.add_argument("file")
    run.add_argument("--sheet")
    run.add_argument("--rows", type=int, default=40)
    run.add_argument("--formulas", action="store_true", help="显示公式而不是结果")
    run.add_argument("--json", action="store_true")
    run.add_argument("--csv", action="store_true", help="额外输出 CSV")
    run.set_defaults(func=cmd_run)

    audit = sub.add_parser("audit", help="查看一个格子的来源与影响")
    audit.add_argument("file")
    audit.add_argument("cell")
    audit.add_argument("--depth", type=int, default=2)
    audit.set_defaults(func=cmd_audit)

    goal = sub.add_parser("goal", help="目标搜索：反解一个输入格")
    goal.add_argument("file")
    goal.add_argument("--target", required=True)
    goal.add_argument("--variable", required=True)
    goal.add_argument("--value", type=float, required=True)
    goal.add_argument("--low", type=float)
    goal.add_argument("--high", type=float)
    goal.add_argument("--save", help="把解写回这个文件")
    goal.set_defaults(func=cmd_goal)

    sens = sub.add_parser("sensitivity", help="敏感性分析")
    sens.add_argument("file")
    sens.add_argument("--target", required=True)
    sens.add_argument("--variable", required=True)
    sens.add_argument("--values", required=True, help="逗号分隔的候选值")
    sens.set_defaults(func=cmd_sensitivity)

    functions = sub.add_parser("functions", help="列出内置函数")
    functions.add_argument("--filter")
    functions.set_defaults(func=cmd_functions)

    demo = sub.add_parser("demo", help="运行内置示例")
    demo.set_defaults(func=cmd_demo)

    serve = sub.add_parser("serve", help="启动网页工作台")
    serve.add_argument("file", nargs="?", help="要打开的工作簿；不填则用内置示例")
    serve.add_argument("--port", type=int, default=8798)
    serve.add_argument("--no-open", action="store_true")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    enable_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
