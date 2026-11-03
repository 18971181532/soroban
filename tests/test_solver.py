import unittest

from soroban import Workbook
from soroban.solver import goal_seek, sensitivity, what_if
from soroban.values import Err


def loan_book():
    wb = Workbook()
    wb.update({
        "B1": "1200000",
        "B2": "0.049",
        "B3": "30",
        "B4": "=PMT(B2/12,B3*12,-B1)",
        "B5": "=B4*B3*12",
    })
    return wb


class GoalSeekTest(unittest.TestCase):
    def test_solves_term(self):
        wb = loan_book()
        result = goal_seek(wb, "B4", "B3", 6000.0)
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.variable_value, 34.692, places=2)
        self.assertAlmostEqual(result.achieved, 6000.0, places=3)
        self.assertGreater(result.iterations, 0)

    def test_does_not_mutate_by_default(self):
        wb = loan_book()
        goal_seek(wb, "B4", "B3", 6000.0)
        self.assertEqual(wb.raw("B3"), "30")
        self.assertAlmostEqual(wb.get("B4"), 6368.7206, places=4)

    def test_apply_writes_solution(self):
        wb = loan_book()
        result = goal_seek(wb, "B4", "B3", 6000.0, apply=True)
        self.assertTrue(result.success)
        self.assertAlmostEqual(wb.get("B4"), 6000.0, places=3)
        self.assertNotEqual(wb.raw("B3"), "30")

    def test_explicit_bracket(self):
        wb = loan_book()
        result = goal_seek(wb, "B4", "B2", 8000.0, low=0.001, high=0.2)
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.variable_value, 0.0702, places=3)
        self.assertEqual(wb.raw("B2"), "0.049")

    def test_no_solution(self):
        wb = loan_book()
        result = goal_seek(wb, "B4", "B3", -1.0)
        self.assertFalse(result.success)
        self.assertEqual(wb.raw("B3"), "30")

    def test_formula_variable_is_rejected(self):
        wb = loan_book()
        with self.assertRaises(ValueError):
            goal_seek(wb, "B4", "B5", 1.0)

    def test_non_numeric_target(self):
        wb = Workbook()
        wb.update({"A1": "1", "A2": '=IF(A1>0,"正","负")'})
        result = goal_seek(wb, "A2", "A1", 1.0)
        self.assertFalse(result.success)
        self.assertTrue(result.message)
        self.assertEqual(wb.raw("A1"), "1")

    def test_already_solved(self):
        wb = Workbook()
        wb.update({"A1": "2", "A2": "=A1*2"})
        result = goal_seek(wb, "A2", "A1", 4.0)
        self.assertTrue(result.success)
        self.assertEqual(result.iterations, 0)
        self.assertEqual(wb.raw("A1"), "2")


class SensitivityTest(unittest.TestCase):
    def test_rows(self):
        wb = loan_book()
        rows = sensitivity(wb, "B4", "B2", [0.03, 0.04, 0.05])
        self.assertEqual([rate for rate, _ in rows], [0.03, 0.04, 0.05])
        self.assertAlmostEqual(rows[0][1], 5059.25, places=2)
        self.assertGreater(rows[2][1], rows[1][1])

    def test_restores_variable(self):
        wb = loan_book()
        sensitivity(wb, "B4", "B2", [0.03, 0.04])
        self.assertEqual(wb.raw("B2"), "0.049")
        self.assertAlmostEqual(wb.get("B4"), 6368.7206, places=4)

    def test_single_value(self):
        wb = loan_book()
        rows = sensitivity(wb, "B4", "B3", [20])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0][1], 7853.3286, places=3)


class WhatIfTest(unittest.TestCase):
    def test_returns_new_workbook(self):
        wb = loan_book()
        trial = what_if(wb, {"B3": "20"})
        self.assertAlmostEqual(trial.get("B4"), 7853.3286, places=3)
        self.assertAlmostEqual(wb.get("B4"), 6368.7206, places=4)

    def test_changes_are_applied(self):
        wb = Workbook()
        wb.update({"A1": "1", "A2": "=A1+1"})
        trial = what_if(wb, {"A1": "10"})
        self.assertEqual(trial.get("A2"), 11.0)
        self.assertEqual(wb.get("A2"), 2.0)


class GoalResultTest(unittest.TestCase):
    def test_dict_payload(self):
        wb = Workbook()
        wb.update({"A1": "1", "A2": "=A1*3"})
        payload = goal_seek(wb, "A2", "A1", 12.0).as_dict()
        self.assertEqual(set(payload), {
            "target", "variable", "desired", "achieved",
            "variable_value", "iterations", "success", "message",
        })
        self.assertTrue(payload["success"])
        self.assertAlmostEqual(payload["variable_value"], 4.0, places=6)


if __name__ == "__main__":
    unittest.main()
