"""随机化不变量测试。

这类测试比手写用例更能抓住重算调度的 bug：

* 同样的公式，按不同顺序写入，结果必须一致；
* 一串随机修改之后，"增量重算"的结果必须和"从零全量重算"逐格相同。
"""

import random
import unittest

from soroban import Workbook
from soroban.values import display


def make_spec(rng: random.Random, size: int = 24):
    spec = {}
    for index in range(1, size + 1):
        ref = f"A{index}"
        roll = rng.random()
        if index <= 3 or roll < 0.28:
            spec[ref] = str(rng.randint(-30, 30))
        elif roll < 0.48:
            start = rng.randint(1, index - 1)
            spec[ref] = f"=SUM(A{start}:A{index - 1})"
        elif roll < 0.62:
            spec[ref] = f"=IF(A{index - 1}>0,A{index - 1}*2,A{index - 1}-1)"
        elif roll < 0.82:
            left = rng.randint(1, index - 1)
            right = rng.randint(1, index - 1)
            spec[ref] = f"=A{left}+A{right}"
        else:
            source = rng.randint(1, index - 1)
            spec[ref] = f"=A{source}*2"
    return spec


def build(spec, order):
    workbook = Workbook()
    for ref in order:
        workbook.set(ref, spec[ref])
    return workbook


def snapshot(workbook, spec):
    return {ref: display(workbook.get(ref)) for ref in spec}


class OrderIndependenceTest(unittest.TestCase):
    def test_random_specs_are_order_independent(self):
        rng = random.Random(20261103)
        for _ in range(12):
            spec = make_spec(rng)
            sorted_order = sorted(spec)
            shuffled = list(sorted_order)
            rng.shuffle(shuffled)
            first = snapshot(build(spec, sorted_order), spec)
            second = snapshot(build(spec, shuffled), spec)
            self.assertEqual(first, second)


class IncrementalEqualsFullTest(unittest.TestCase):
    def test_after_random_edits(self):
        rng = random.Random(7)
        for _ in range(10):
            spec = make_spec(rng, size=20)
            workbook = build(spec, sorted(spec))
            for _ in range(6):
                ref = f"A{rng.randint(1, 20)}"
                workbook.set(ref, str(rng.randint(-60, 60)))
            fresh = Workbook.from_dict(workbook.to_dict())
            self.assertEqual(snapshot(workbook, spec), snapshot(fresh, spec))
            self.assertTrue(fresh.stats.full)

    def test_after_mixed_edits(self):
        rng = random.Random(11)
        for _ in range(6):
            spec = make_spec(rng, size=16)
            workbook = build(spec, sorted(spec))
            for _ in range(4):
                ref = f"A{rng.randint(1, 16)}"
                other = rng.randint(1, 16)
                workbook.set(ref, f"=A{other}+1")
            fresh = Workbook.from_dict(workbook.to_dict())
            self.assertEqual(snapshot(workbook, spec), snapshot(fresh, spec))


class RangeEdgeTest(unittest.TestCase):
    def test_reversed_range_in_formula(self):
        workbook = Workbook()
        workbook.update({"A1": "1", "A2": "2", "A3": "3", "B1": "=SUM(A3:A1)"})
        self.assertEqual(workbook.get("B1"), 6.0)

    def test_whole_column_formula(self):
        workbook = Workbook()
        workbook.update({"B1": "=SUM(A1:A1048576)", "A1": "1", "A2": "2"})
        self.assertEqual(workbook.get("B1"), 3.0)


if __name__ == "__main__":
    unittest.main()
