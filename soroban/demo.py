"""内置示例工作簿：一份季度经营表 + 一份贷款测算。

示例不只是"看起来热闹"，它同时是 README、网页初始状态和基准测试的
共同数据源——改一处，三处同步。
"""

from __future__ import annotations

from typing import List, Tuple

from .workbook import Workbook

BUDGET = "经营"
LOAN = "贷款"

MONTHS = [
    ("1月", 128_000, 76_000),
    ("2月", 142_000, 81_000),
    ("3月", 156_000, 88_000),
    ("4月", 149_000, 84_500),
    ("5月", 171_000, 93_000),
    ("6月", 186_000, 99_000),
]


def build_demo() -> Workbook:
    wb = Workbook([BUDGET, LOAN])
    changes = {
        f"{BUDGET}!A1": "月份", f"{BUDGET}!B1": "收入", f"{BUDGET}!C1": "成本",
        f"{BUDGET}!D1": "毛利", f"{BUDGET}!E1": "毛利率",
        f"{BUDGET}!A8": "合计", f"{BUDGET}!A9": "月均", f"{BUDGET}!A10": "最佳月份",
        f"{BUDGET}!A12": "关键指标", f"{BUDGET}!A13": "综合毛利率",
        f"{BUDGET}!A14": "成本占比", f"{BUDGET}!A15": "年还款额",
        f"{LOAN}!A1": "贷款测算", f"{LOAN}!A2": "本金", f"{LOAN}!B2": "1200000",
        f"{LOAN}!A3": "年利率", f"{LOAN}!B3": "0.049",
        f"{LOAN}!A4": "年限（年）", f"{LOAN}!B4": "30",
        f"{LOAN}!A5": "期数（月）", f"{LOAN}!B5": "=B4*12",
        f"{LOAN}!A6": "月供", f"{LOAN}!B6": "=PMT(B3/12,B5,-B2)",
        f"{LOAN}!A7": "总还款", f"{LOAN}!B7": "=B6*B5",
        f"{LOAN}!A8": "总利息", f"{LOAN}!B8": "=B7-B2",
        f"{LOAN}!A10": "利率敏感性", f"{LOAN}!A11": "年利率",
        f"{LOAN}!B11": "月供", f"{LOAN}!C11": "总利息",
    }

    for index, (month, income, cost) in enumerate(MONTHS, start=2):
        changes[f"{BUDGET}!A{index}"] = month
        changes[f"{BUDGET}!B{index}"] = str(income)
        changes[f"{BUDGET}!C{index}"] = str(cost)
        changes[f"{BUDGET}!D{index}"] = f"=B{index}-C{index}"
        changes[f"{BUDGET}!E{index}"] = f"=D{index}/B{index}"

    last = 1 + len(MONTHS)
    changes.update({
        f"{BUDGET}!B8": f"=SUM(B2:B{last})",
        f"{BUDGET}!C8": f"=SUM(C2:C{last})",
        f"{BUDGET}!D8": f"=SUM(D2:D{last})",
        f"{BUDGET}!E8": "=D8/B8",
        f"{BUDGET}!B9": f"=AVERAGE(B2:B{last})",
        f"{BUDGET}!C9": f"=AVERAGE(C2:C{last})",
        f"{BUDGET}!D9": f"=AVERAGE(D2:D{last})",
        f"{BUDGET}!B10": f"=INDEX(A2:A{last},MATCH(MAX(D2:D{last}),D2:D{last},0))",
        f"{BUDGET}!D13": "=TEXT(D8/B8,\"0.0%\")",
        f"{BUDGET}!D14": "=TEXT(C8/B8,\"0.0%\")",
        f"{BUDGET}!D15": f"={LOAN}!B6*12",
    })

    for offset, rate in enumerate((0.035, 0.042, 0.049, 0.056, 0.063), start=12):
        changes[f"{LOAN}!A{offset}"] = f"{rate:.3f}"
        changes[f"{LOAN}!B{offset}"] = f"=PMT(A{offset}/12,$B$5,-$B$2)"
        changes[f"{LOAN}!C{offset}"] = f"=B{offset}*$B$5-$B$2"

    wb.update(changes, strict=False)
    wb.recalculate()
    return wb


def budget_rows(wb: Workbook, limit: int = 16) -> Tuple[List[str], List[List[str]]]:
    """把示例表整理成可直接打印的二维文本。"""
    sheet = wb.sheet(BUDGET)
    headers = ["", "A", "B", "C", "D", "E"]
    rows: List[List[str]] = []
    for row in range(limit):
        line = [str(row + 1)]
        empty = True
        for col in range(5):
            cell = sheet.cell(row, col)
            if cell is None:
                line.append("")
                continue
            empty = False
            line.append(wb.text(f"{BUDGET}!{chr(65 + col)}{row + 1}"))
        if empty and row > 14:
            break
        rows.append(line)
    return headers, rows


def headline(wb: Workbook) -> List[str]:
    """示例里最值得看的几个数字。"""
    return [
        f"半年收入 {wb.text(f'{BUDGET}!B8')}，毛利 {wb.text(f'{BUDGET}!D8')}",
        f"综合毛利率 {wb.text(f'{BUDGET}!D13')}，成本占比 {wb.text(f'{BUDGET}!D14')}",
        f"最佳月份 {wb.text(f'{BUDGET}!B10')}",
        f"贷款月供 {wb.text(f'{LOAN}!B6')}，总利息 {wb.text(f'{LOAN}!B8')}",
    ]
