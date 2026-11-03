import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from soroban.demo import build_demo
from soroban.server import make_server


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = make_server(build_demo(), port=0)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.post("/api/reset", {})

    # -- 辅助 -------------------------------------------------------------
    def get(self, path, raw=False):
        with urlopen(self.base + path, timeout=5) as response:
            body = response.read()
            return response.status, body if raw else json.loads(body.decode("utf-8"))

    def post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        request = Request(self.base + path, data=data, method="POST",
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def state(self):
        return self.get("/api/state")[1]

    @staticmethod
    def find(state, sheet, ref):
        for item in state["sheets"]:
            if item["name"] == sheet:
                for cell in item["cells"]:
                    if cell["ref"] == ref:
                        return cell
        return None

    # -- 读 ---------------------------------------------------------------
    def test_health(self):
        status, data = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_state_has_both_sheets(self):
        state = self.state()
        self.assertEqual([sheet["name"] for sheet in state["sheets"]], ["经营", "贷款"])
        cell = self.find(state, "经营", "D8")
        self.assertEqual(cell["display"], "410500")
        self.assertEqual(cell["raw"], "=SUM(D2:D7)")

    def test_audit(self):
        data = self.get("/api/audit?cell=%E7%BB%8F%E8%90%A5%21D8")[1]
        self.assertEqual(data["ref"], "经营!D8")
        self.assertEqual(data["raw"], "=SUM(D2:D7)")
        self.assertEqual(len(data["tree"]["precedents"]), 6)
        self.assertIn("=SUM(D2:D7)", data["explain"])

    def test_functions_endpoint(self):
        data = self.get("/api/functions?q=PMT")[1]
        names = [item["name"] for item in data["functions"]]
        self.assertIn("PMT", names)
        self.assertLess(len(names), 8)

    def test_index_page(self):
        status, body = self.get("/", raw=True)
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("Soroban", text)
        self.assertIn("app.js", text)

    def test_static_asset(self):
        status, body = self.get("/static/app.js", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"runGoal", body)

    def test_unknown_route(self):
        with self.assertRaises(HTTPError) as caught:
            self.get("/api/nope")
        self.assertEqual(caught.exception.code, 404)

    # -- 写 ---------------------------------------------------------------
    def test_set_changes_dependents(self):
        status, state = self.post("/api/set", {"changes": {"经营!B2": "200000"}})
        self.assertEqual(status, 200)
        self.assertEqual(self.find(state, "经营", "B2")["display"], "200000")
        self.assertEqual(self.find(state, "经营", "D8")["display"], "482500")
        self.assertEqual(state["stats"]["computed"], 10)

    def test_set_clears_cell(self):
        self.post("/api/set", {"changes": {"经营!A2": ""}})
        state = self.state()
        self.assertIsNone(self.find(state, "经营", "A2"))

    def test_bad_formula_is_stored_as_error(self):
        _, state = self.post("/api/set", {"changes": {"经营!A20": "=1+"}})
        cell = self.find(state, "经营", "A20")
        self.assertEqual(cell["type"], "error")
        self.assertEqual(cell["display"], "#PARSE!")

    def test_goal_seek(self):
        status, data = self.post("/api/goal", {
            "target": "贷款!B6", "variable": "贷款!B4", "value": 6000,
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["result"]["success"])
        self.assertAlmostEqual(data["result"]["variable_value"], 34.692, places=2)
        self.assertEqual(self.find(data["state"], "贷款", "B4")["display"], "30")

    def test_goal_seek_apply(self):
        _, data = self.post("/api/goal", {
            "target": "贷款!B6", "variable": "贷款!B4", "value": 6000, "apply": True,
        })
        self.assertTrue(data["result"]["success"])
        self.assertNotEqual(self.find(data["state"], "贷款", "B4")["display"], "30")

    def test_goal_missing_fields(self):
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/goal", {"target": "贷款!B6"})
        self.assertEqual(caught.exception.code, 400)

    def test_reset(self):
        self.post("/api/set", {"changes": {"经营!B2": "1"}})
        _, state = self.post("/api/reset", {})
        self.assertEqual(self.find(state, "经营", "B2")["display"], "128000")

    def test_recalc(self):
        _, state = self.post("/api/recalc", {})
        self.assertTrue(state["stats"]["full"])

    def test_invalid_json(self):
        request = Request(self.base + "/api/set", data=b"{oops", method="POST",
                          headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
