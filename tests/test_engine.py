import unittest

from soroban import Workbook
from soroban.values import Err


class RecalcTest(unittest.TestCase):
    def setUp(self):
        self.wb = Workbook()
        self.wb.update({
            "A1": "10", "A2": "20",
            "A3": "=SUM(A1:A2)", "A4": "=A3*2", "A5": "=A4+1",
            "B1": "1", "B2": "=B1+1",
        })

    def test_initial_values(self):
        self.assertEqual(self.wb.get("A3"), 30.0)
        self.assertEqual(self.wb.get("A4"), 60.0)
        self.assertEqual(self.wb.get("A5"), 61.0)
        self.assertEqual(self.wb.get("B2"), 2.0)

    def test_incremental_only_recomputes_dependents(self):
        stats = self.wb.set("A1", "100")
        self.assertFalse(stats.full)
        self.assertEqual(stats.computed, 3)
        self.assertEqual(self.wb.get("A3"), 120.0)
        self.assertEqual(self.wb.get("A5"), 241.0)

    def test_unrelated_change_computes_nothing(self):
        stats = self.wb.set("B1", "5")
        self.assertEqual(stats.computed, 1)
        stats = self.wb.set("C1", "新值")
        self.assertEqual(stats.computed, 0)

    def test_full_recalc(self):
        stats = self.wb.recalculate()
        self.assertTrue(stats.full)
        self.assertEqual(stats.computed, 4)

    def test_forward_reference(self):
        workbook = Workbook()
        workbook.update({"A1": "=A2+1", "A2": "=A3+1", "A3": "1"})
        self.assertEqual(workbook.get("A1"), 3.0)

    def test_insertion_order_does_not_matter(self):
        forward = Workbook()
        forward.update({"A1": "1", "A2": "=A1+1", "A3": "=A2+1"})
        backward = Workbook()
        backward.update({"A3": "=A2+1", "A2": "=A1+1", "A1": "1"})
        self.assertEqual([forward.get(cell) for cell in ("A1", "A2", "A3")],
                         [backward.get(cell) for cell in ("A1", "A2", "A3")])

    def test_range_dependency(self):
        workbook = Workbook()
        workbook.update({"A1": "=SUM(B1:B3)", "B1": "1", "B2": "2", "B3": "3"})
        self.assertEqual(workbook.get("A1"), 6.0)
        stats = workbook.set("B2", "20")
        self.assertEqual(stats.computed, 1)
        self.assertEqual(workbook.get("A1"), 24.0)

    def test_changing_formula_unhooks_old_edges(self):
        workbook = Workbook()
        workbook.update({"A1": "=B1", "B1": "1", "C1": "2"})
        workbook.set("A1", "=C1")
        self.assertEqual(workbook.dependents("B1"), [])
        self.assertEqual(workbook.dependents("C1"), ["Sheet1!A1"])
        stats = workbook.set("B1", "9")
        self.assertEqual(stats.computed, 0)

    def test_cross_sheet(self):
        workbook = Workbook(["数据", "汇总"])
        workbook.update({"汇总!A1": "=数据!A1*2", "数据!A1": "5"})
        self.assertEqual(workbook.get("汇总!A1"), 10.0)
        workbook.set("数据!A1", "7")
        self.assertEqual(workbook.get("汇总!A1"), 14.0)

    def test_blank_reference_is_zero(self):
        workbook = Workbook()
        workbook.update({"A1": "=B1+1"})
        self.assertEqual(workbook.get("A1"), 1.0)

    def test_text_in_arithmetic_is_an_error(self):
        workbook = Workbook()
        workbook.update({"A1": "文本", "A2": "=A1+1"})
        value = workbook.get("A2")
        self.assertIsInstance(value, Err)
        self.assertEqual(value.code, "#VALUE!")


class CycleTest(unittest.TestCase):
    def test_mutual_cycle(self):
        workbook = Workbook()
        workbook.update({"A1": "=A2+1", "A2": "=A1+1"})
        self.assertEqual(workbook.get("A1"), Err("#CYCLE!"))
        self.assertEqual(workbook.get("A2"), Err("#CYCLE!"))
        self.assertEqual(workbook.stats.cycles, 1)

    def test_self_cycle(self):
        workbook = Workbook()
        workbook.update({"A1": "=A1+1"})
        self.assertEqual(workbook.get("A1"), Err("#CYCLE!"))

    def test_downstream_of_cycle(self):
        workbook = Workbook()
        workbook.update({"A1": "=A2", "A2": "=A1", "A3": "=A1+1"})
        self.assertEqual(workbook.get("A3"), Err("#CYCLE!"))

    def test_fixing_cycle(self):
        workbook = Workbook()
        workbook.update({"A1": "=A2", "A2": "=A1", "A3": "=A1+1"})
        stats = workbook.set("A2", "5")
        self.assertEqual(stats.cycles, 0)
        self.assertEqual(workbook.get("A1"), 5.0)
        self.assertEqual(workbook.get("A3"), 6.0)

    def test_range_self_reference_is_a_cycle(self):
        workbook = Workbook()
        workbook.update({"A1": "=SUM(A1:A2)", "A2": "1"})
        self.assertEqual(workbook.get("A1"), Err("#CYCLE!"))


class IterativeTest(unittest.TestCase):
    def test_converges(self):
        workbook = Workbook(iterative=True)
        workbook.update({"A1": "=(A1+10)/2"})
        value = workbook.get("A1")
        self.assertAlmostEqual(value, 10.0, places=6)
        self.assertTrue(workbook.stats.converged)
        self.assertGreater(workbook.stats.iterations, 1)

    def test_does_not_converge(self):
        workbook = Workbook(iterative=True, max_iterations=10)
        workbook.update({"A1": "=A1+1"})
        self.assertEqual(workbook.get("A1"), 10.0)
        self.assertFalse(workbook.stats.converged)

    def test_off_by_default(self):
        workbook = Workbook()
        workbook.update({"A1": "=(A1+10)/2"})
        self.assertEqual(workbook.get("A1"), Err("#CYCLE!"))


class VolatileTest(unittest.TestCase):
    def test_rand_changes_every_recalc(self):
        workbook = Workbook()
        workbook.update({"A1": "=RAND()"})
        values = set()
        for _ in range(8):
            workbook.recalculate()
            values.add(float(workbook.get("A1")))
        self.assertGreater(len(values), 6)

    def test_volatile_is_recomputed_on_unrelated_edit(self):
        workbook = Workbook()
        workbook.update({"A1": "=RAND()", "B1": "1"})
        stats = workbook.set("B1", "2")
        self.assertGreaterEqual(stats.computed, 1)


class ProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.wb = Workbook()
        self.wb.update({
            "A1": "1", "A2": "2",
            "B1": "=A1+A2",
            "C1": "=B1*2",
        })

    def test_contributors(self):
        keys = self.wb.engine.contributors(self.wb.target_key("C1"))
        names = {self.wb.engine._ref_name(key) for key in keys}
        self.assertEqual(names, {"Sheet1!A1", "Sheet1!A2", "Sheet1!B1"})

    def test_precedents(self):
        self.assertEqual(self.wb.precedents("C1"), ["Sheet1!B1"])

    def test_dependents(self):
        self.assertEqual(self.wb.dependents("A1"), ["Sheet1!B1"])
        self.assertEqual(self.wb.dependents("C1"), [])

    def test_audit_tree(self):
        tree = self.wb.audit("C1", depth=2)
        self.assertEqual(tree["ref"], "Sheet1!C1")
        self.assertEqual(tree["formula"], "=B1*2")
        precedents = tree["precedents"]
        self.assertEqual(len(precedents), 1)
        self.assertEqual(precedents[0]["ref"], "Sheet1!B1")
        self.assertEqual(len(precedents[0]["precedents"]), 2)

    def test_explain_mentions_source_and_consumers(self):
        text = self.wb.explain("B1")
        self.assertIn("=A1+A2", text)
        self.assertIn("Sheet1!C1", text)

    def test_audit_of_blank(self):
        tree = self.wb.audit("D9", depth=1)
        self.assertEqual(tree["kind"], "blank")


class ErrorTest(unittest.TestCase):
    def test_error_propagates(self):
        workbook = Workbook()
        workbook.update({"A1": "=1/0", "A2": "=A1+1", "A3": "=IFERROR(A2,0)"})
        self.assertEqual(workbook.get("A2"), Err("#DIV/0!"))
        self.assertEqual(workbook.get("A3"), 0.0)

    def test_strict_parse_error_raises(self):
        workbook = Workbook()
        with self.assertRaises(Exception):
            workbook.set("A1", "=1+")

    def test_lenient_parse_error_is_stored(self):
        workbook = Workbook()
        workbook.update({"A1": "=1+"})
        value = workbook.get("A1")
        self.assertIsInstance(value, Err)
        self.assertEqual(value.code, "#PARSE!")

    def test_fixing_bad_formula(self):
        workbook = Workbook()
        workbook.update({"A1": "=1+"})
        workbook.set("A1", "=1+1")
        self.assertEqual(workbook.get("A1"), 2.0)


class StatsTest(unittest.TestCase):
    def test_seconds_is_positive(self):
        workbook = Workbook()
        workbook.update({"A1": "=1+1"})
        self.assertGreater(workbook.stats.seconds, 0.0)

    def test_payload_keys(self):
        workbook = Workbook()
        workbook.update({"A1": "=1+1"})
        payload = workbook.stats.as_dict()
        self.assertEqual(set(payload), {"computed", "cycles", "iterations", "converged", "seconds", "full"})


if __name__ == "__main__":
    unittest.main()
