#!/usr/bin/env python3
"""生成 README 用的图与数据：``python run_demos.py``。

两张图都由真实运行结果驱动：

* ``docs/deps.svg``   —— 从引擎的依赖关系里读出来，不是手画的
* ``docs/recalc.svg`` —— 现场跑 2k / 8k / 20k 行，记录耗时
"""

from __future__ import annotations

import math
import os
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from soroban import Workbook
from soroban.console import enable_utf8, table

INK = "#241f1a"
MUTED = "#857a6b"
LINE = "#e3dacb"
PAPER = "#fffdf8"
ACCENT = "#0f766e"
AMBER = "#b45309"
BLUE = "#2563eb"
DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _text(x, y, text, size=12, fill=INK, anchor="start", weight="normal", family=None):
    family = family or "Inter, 'Segoe UI', 'Microsoft YaHei', sans-serif"
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'font-family="{family}">{_esc(text)}</text>')


def _measure(text: str, size: float) -> float:
    """粗略估算一段文字的宽度，用来给标注框留位置。

    中日韩字符按一个字号宽算，西文按 0.55 倍字号算——够用就好，
    不追求和浏览器完全一致。
    """
    total = 0.0
    for char in str(text):
        total += size if ord(char) > 0x2E7F else size * 0.55
    return total


def _svg(width, height, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">\n'
            f'<rect width="{width}" height="{height}" fill="{PAPER}"/>\n'
            f'{body}\n</svg>\n')


def _write(name: str, content: str) -> None:
    os.makedirs(DOCS, exist_ok=True)
    path = os.path.join(DOCS, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    print(f"  写出 {path}")


# --------------------------------------------------------------------------
# 依赖图
# --------------------------------------------------------------------------
def _edge(x1, y1, x2, y2) -> str:
    if abs(x2 - x1) > abs(y2 - y1):
        bend = max(24.0, abs(x2 - x1) * 0.45)
        path = f"M{x1:.1f},{y1:.1f} C{x1 + bend:.1f},{y1:.1f} {x2 - bend:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"
    else:
        bend = max(22.0, abs(y2 - y1) * 0.45)
        path = f"M{x1:.1f},{y1:.1f} C{x1:.1f},{y1 + bend:.1f} {x2:.1f},{y2 - bend:.1f} {x2:.1f},{y2:.1f}"
    return (f'<path d="{path}" fill="none" stroke="#c9bda9" stroke-width="1.4" '
            f'marker-end="url(#arrow)"/>')


def dependency_svg() -> str:
    workbook = Workbook(["示例"])
    workbook.update({
        "A1": "12", "A2": "18", "A3": "30",
        "B1": "=SUM(A1:A3)",
        "C1": "=B1*1.2",
        "D1": "=ROUND(C1,0)",
        "E1": '=TEXT(C1,"0.0%")',
    })

    positions = {
        "A1": (54, 74), "A2": (54, 148), "A3": (54, 222),
        "B1": (212, 140), "C1": (396, 138),
        "D1": (600, 66), "E1": (600, 212),
    }
    sizes = {
        "A1": (104, 46), "A2": (104, 46), "A3": (104, 46),
        "B1": (146, 54), "C1": (136, 54),
        "D1": (132, 50), "E1": (178, 50),
    }
    labels = {
        "B1": "=SUM(A1:A3)", "C1": "=B1*1.2",
        "D1": "=ROUND(C1,0)", "E1": '=TEXT(C1,"0.0%")',
    }

    body: List[str] = [
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#b9ab97"/></marker></defs>',
        _text(30, 32, "一个格子的依赖图", 15, INK, weight="bold"),
        _text(30, 50, "B1 汇总 A1:A3；改 A2 时只会重算它下游的 4 个格子。", 10.5, MUTED),
    ]

    # 先画边，后画节点，避免线压在文字上
    from soroban.refs import col_to_letters

    for ref, (x, y) in positions.items():
        width, height = sizes[ref]
        # 用 precedent_keys 而不是 precedents：后者对区域只给一个
        # "A1:A3" 的字符串，画图时需要展开成三条边。
        precedents = []
        for precedent in workbook.engine.precedent_keys(workbook.target_key(ref)):
            cell = f"{col_to_letters(precedent[2])}{precedent[1] + 1}"
            if cell in positions:
                precedents.append(cell)
        # 多条边不要挤在同一个点上，沿目标框的左边缘均匀分开
        for index, cell in enumerate(precedents):
            px, py = positions[cell]
            pw, ph = sizes[cell]
            entry = y + height * (index + 1) / (len(precedents) + 1)
            body.append(_edge(px + pw, py + ph / 2, x, entry))

    for ref, (x, y) in positions.items():
        width, height = sizes[ref]
        body.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="9" '
            f'fill="#fffdf8" stroke="{LINE}" stroke-width="1.2"/>'
        )
        formula = labels.get(ref)
        body.append(_text(x + 12, y + (21 if formula else 28), ref, 13, ACCENT,
                          weight="bold", family="Consolas, monospace"))
        if formula:
            body.append(_text(x + 12, y + 37, formula, 9.5, MUTED,
                              family="Consolas, monospace"))
        body.append(_text(x + width - 10, y + (37 if formula else 31),
                          workbook.text(ref), 9.5, AMBER, "end",
                          family="Consolas, monospace"))

    body.append(_text(30, 300, "箭头方向：被依赖 → 依赖它的公式。区域引用会展开成每一条边。",
                      9.5, MUTED))
    return _svg(810, 316, "\n".join(body))


# --------------------------------------------------------------------------
# 重算基准
# --------------------------------------------------------------------------
def benchmark(sizes=(500, 2000, 8000, 20000)) -> List[Tuple[int, float, float, float]]:
    results = []
    for rows in sizes:
        workbook = Workbook()
        changes: Dict[str, str] = {}
        for index in range(1, rows + 1):
            changes[f"A{index}"] = str(index)
            changes[f"B{index}"] = f"=A{index}*2"
            changes[f"C{index}"] = f"=B{index}+1"
        started = time.perf_counter()
        workbook.update(changes)
        build = time.perf_counter() - started

        started = time.perf_counter()
        workbook.recalculate()
        full = time.perf_counter() - started

        started = time.perf_counter()
        workbook.set("A1", "999")
        incremental = time.perf_counter() - started
        results.append((rows, build, full, incremental))
    return results


def benchmark_svg(results) -> str:
    width, height = 690, 340
    left, right, top, bottom = 66, 54, 104, 54
    plot_w, plot_h = width - left - right, height - top - bottom
    full_values = [row[2] * 1000 for row in results]
    incr_values = [row[3] * 1000 for row in results]

    log_min, log_max = -2.0, 3.0
    def y_of(ms):
        value = max(log_min, min(log_max, math.log10(max(ms, 1e-4))))
        return top + plot_h * (1 - (value - log_min) / (log_max - log_min))

    body = [
        _text(28, 30, "改一个格子有多快？", 15, INK, weight="bold"),
        _text(28, 49, "同一份表（40,000 个公式），全量重算 vs 增量重算；纵轴为对数刻度。",
              10, MUTED),
    ]

    for exponent in range(int(log_min), int(log_max) + 1):
        y = y_of(10 ** exponent)
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
                    f'stroke="#ece4d6" stroke-width="1"/>')
        label = f"{10 ** exponent:g}"
        body.append(_text(left - 10, y + 4, label, 9, "#a99e8d", "end"))
    body.append(_text(20, top - 16, "毫秒", 9, MUTED))

    step = plot_w / max(1, len(results) - 1)
    def x_of(index):
        return left + step * index

    for index, row in enumerate(results):
        body.append(_text(x_of(index), height - 26, f"{row[0]:,} 行", 10, "#7a6a58", "middle"))

    def series(values, color):
        points = " ".join(f"{x_of(i):.1f},{y_of(value):.1f}" for i, value in enumerate(values))
        body.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.4" '
                    f'stroke-linejoin="round" points="{points}"/>')
        for i, value in enumerate(values):
            body.append(f'<circle cx="{x_of(i):.1f}" cy="{y_of(value):.1f}" r="3.6" fill="{color}"/>')

    series(full_values, ACCENT)
    series(incr_values, AMBER)

    for i, value in enumerate(full_values):
        body.append(_text(x_of(i) + 6, y_of(value) - 10, f"{value:.1f}", 9.5, ACCENT))
    body.append(_text(x_of(len(results) - 1) + 6, y_of(incr_values[-1]) + 4,
                      f"{incr_values[-1]:.2f}", 9.5, AMBER))

    # 图例
    legend_y = 68
    body.append(f'<circle cx="{width - 190}" cy="{legend_y}" r="4" fill="{ACCENT}"/>')
    body.append(_text(width - 180, legend_y + 4, "全量重算", 10, INK))
    body.append(f'<circle cx="{width - 108}" cy="{legend_y}" r="4" fill="{AMBER}"/>')
    body.append(_text(width - 98, legend_y + 4, "增量重算", 10, INK))

    speedup = full_values[-1] / incr_values[-1]
    note = f"2 万行时增量快 {speedup:,.0f}×"
    note_width = _measure(note, 11) + 22
    body.append(f'<rect x="22" y="57" width="{note_width:.0f}" height="22" rx="11" '
                f'fill="#fbf1e0" stroke="#eeddc2" stroke-width="1"/>')
    body.append(_text(33, 72, note, 11, AMBER, weight="bold"))
    body.append(_text(left, height - 8, "增量只重算被改动格子的下游，与整表规模基本无关。",
                      9.5, MUTED))
    return _svg(width, height, "\n".join(body))


def cmd_graph() -> None:
    print("生成依赖图……")
    _write("deps.svg", dependency_svg())


def cmd_bench() -> None:
    print("跑基准（会新建 3 万多个公式）……")
    results = benchmark()
    rows = [[f"{row[0]:,}", f"{row[1] * 1000:.0f}", f"{row[2] * 1000:.1f}",
             f"{row[3] * 1000:.2f}"] for row in results]
    print(table(["公式规模", "建表 ms", "全量 ms", "增量 ms"], rows,
                ["right", "right", "right", "right"]))
    _write("recalc.svg", benchmark_svg(results))
    ratio = results[-1][2] / results[-1][3]
    print(f"  最大规模下增量快 {ratio:.0f}×；全量约 {results[-1][0] * 2 / results[-1][2]:,.0f} 公式/秒")


def main() -> int:
    enable_utf8()
    cmd_graph()
    cmd_bench()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
