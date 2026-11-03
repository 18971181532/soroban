"""HTTP 工作台：把工作簿暴露成一组 JSON 接口，并托管静态页面。

接口刻意做成"每次写操作都返回整份状态"：表格通常只有几百个格子，
一次全量传输比维护增量补丁简单得多，前端也不会出现状态漂移。
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .demo import build_demo
from .functions import catalogue
from .refs import col_to_letters
from .solver import goal_seek, sensitivity
from .values import Err, display, is_number
from .workbook import Workbook

WEB_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
)


def _cell_type(value) -> str:
    if isinstance(value, Err):
        return "error"
    if isinstance(value, bool):
        return "bool"
    if is_number(value):
        return "number"
    if value is None:
        return "blank"
    return "text"


def sheet_payload(workbook: Workbook, name: str) -> Dict[str, object]:
    sheet = workbook.sheets[name]
    max_row, max_col = sheet.bounds()
    cells = []
    for (row, col), cell in sorted(sheet.cells.items()):
        value = workbook.engine.values.get((name, row, col))
        cells.append({
            "ref": f"{col_to_letters(col)}{row + 1}",
            "row": row,
            "col": col,
            "raw": cell.raw,
            "display": display(value),
            "type": _cell_type(value),
            "kind": cell.kind,
            "error": value.code if isinstance(value, Err) else None,
        })
    return {
        "name": name,
        "cells": cells,
        "rows": max_row + 1,
        "cols": max_col + 1,
    }


def state_payload(workbook: Workbook) -> Dict[str, object]:
    return {
        "version": __version__,
        "sheets": [sheet_payload(workbook, name) for name in workbook.sheet_names],
        "stats": workbook.stats.as_dict(),
        "iterative": workbook.engine.iterative,
    }


class Workbench:
    def __init__(self, workbook: Workbook) -> None:
        self.workbook = workbook
        self.lock = threading.RLock()

    def state(self) -> Dict[str, object]:
        with self.lock:
            return state_payload(self.workbook)

    def reset(self) -> Dict[str, object]:
        with self.lock:
            self.workbook = build_demo()
            return state_payload(self.workbook)

    def set_cells(self, changes: Dict[str, str]) -> Dict[str, object]:
        with self.lock:
            self.workbook.update(changes)
            return state_payload(self.workbook)

    def audit(self, cell: str, depth: int = 2) -> Dict[str, object]:
        with self.lock:
            key = self.workbook.target_key(cell)
            raw_cell = self.workbook.cell_at(key)
            return {
                "ref": self.workbook._key_label(key),
                "cell": f"{col_to_letters(key[2])}{key[1] + 1}",
                "sheet": key[0],
                "raw": raw_cell.raw if raw_cell else "",
                "formula": bool(raw_cell and raw_cell.kind == "formula"),
                "kind": raw_cell.kind if raw_cell else "blank",
                "value": self.workbook.text(cell),
                "tree": self.workbook.audit(cell, depth=depth),
                "dependents": self.workbook.dependents(cell),
                "explain": self.workbook.explain(cell),
            }

    def solve(self, target: str, variable: str, value: float, apply: bool) -> Dict[str, object]:
        with self.lock:
            result = goal_seek(self.workbook, target, variable, value, apply=apply)
            return {"result": result.as_dict(), "state": state_payload(self.workbook)}

    def sensitive(self, target: str, variable: str, values) -> Dict[str, object]:
        with self.lock:
            rows = sensitivity(self.workbook, target, variable, values)
            return {
                "rows": [
                    {"input": item, "output": display(value), "type": _cell_type(value)}
                    for item, value in rows
                ],
                "state": state_payload(self.workbook),
            }


class Handler(BaseHTTPRequestHandler):
    workbench: Workbench
    server_version = f"Soroban/{__version__}"

    # 默认的 stderr 日志太吵，压掉
    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover
        pass

    # -- 基础 -------------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: Dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status=status)

    def _read_json(self) -> Dict[str, object]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    # -- 路由 -------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/state":
                self._json(self.workbench.state())
            elif path == "/api/health":
                self._json({"ok": True, "version": __version__})
            elif path == "/api/audit":
                query = parse_qs(parsed.query)
                cell = (query.get("cell") or ["A1"])[0]
                depth = int((query.get("depth") or ["2"])[0])
                self._json(self.workbench.audit(cell, depth))
            elif path == "/api/functions":
                query = parse_qs(parsed.query)
                keyword = (query.get("q") or [""])[0].casefold()
                items = []
                for spec in catalogue():
                    haystack = f"{spec.name} {spec.summary}".casefold()
                    if keyword and keyword not in haystack:
                        continue
                    items.append({
                        "name": spec.name,
                        "params": spec.params,
                        "summary": spec.summary,
                        "category": spec.category,
                        "special": spec.special,
                    })
                self._json({"functions": items})
            elif path == "/":
                self._serve_file("index.html")
            elif path.startswith("/static/"):
                self._serve_file(path[len("/static/"):])
            else:
                self._error("没有这个地址", 404)
        except ValueError as exc:
            self._error(str(exc), 400)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error("请求体不是合法 JSON")
            return

        try:
            if path == "/api/set":
                changes = payload.get("changes")
                if not isinstance(changes, dict) or not changes:
                    cell = payload.get("cell")
                    value = payload.get("value")
                    if not isinstance(cell, str):
                        self._error("需要 changes 或 cell")
                        return
                    changes = {cell: "" if value is None else str(value)}
                clean = {str(key): "" if value is None else str(value)
                         for key, value in changes.items()}
                self._json(self.workbench.set_cells(clean))
            elif path == "/api/audit":
                cell = str(payload.get("cell") or "A1")
                depth = int(payload.get("depth") or 2)
                self._json(self.workbench.audit(cell, depth))
            elif path == "/api/goal":
                target = str(payload.get("target") or "")
                variable = str(payload.get("variable") or "")
                if not target or not variable:
                    self._error("需要 target 与 variable")
                    return
                value = float(payload.get("value"))
                self._json(self.workbench.solve(target, variable, value,
                                                bool(payload.get("apply"))))
            elif path == "/api/sensitivity":
                target = str(payload.get("target") or "")
                variable = str(payload.get("variable") or "")
                values = [float(item) for item in payload.get("values") or []]
                if not target or not variable or not values:
                    self._error("需要 target、variable 与 values")
                    return
                self._json(self.workbench.sensitive(target, variable, values))
            elif path == "/api/reset":
                self._json(self.workbench.reset())
            elif path == "/api/recalc":
                with self.workbench.lock:
                    self.workbench.workbook.recalculate()
                    self._json(state_payload(self.workbench.workbook))
            else:
                self._error("没有这个地址", 404)
        except ValueError as exc:
            self._error(str(exc), 400)

    # -- 静态文件 ---------------------------------------------------------
    def _serve_file(self, relative: str) -> None:
        relative = relative.split("?", 1)[0]
        full = os.path.normpath(os.path.join(WEB_ROOT, relative))
        if not full.startswith(WEB_ROOT) or not os.path.isfile(full):
            self._error("文件不存在", 404)
            return
        content_type, _ = mimetypes.guess_type(full)
        if content_type is None:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript", "application/json", "image/svg+xml",
        ):
            content_type += "; charset=utf-8"
        with open(full, "rb") as handle:
            body = handle.read()
        self._send(200, body, content_type)


def make_server(workbook: Workbook, host: str = "127.0.0.1", port: int = 8798):
    handler = type("BoundHandler", (Handler,), {"workbench": Workbench(workbook)})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def serve(
    workbook: Optional[Workbook] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8798,
    open_browser: bool = True,
) -> None:
    workbook = workbook or build_demo()
    try:
        httpd = make_server(workbook, host, port)
    except OSError as exc:
        raise SystemExit(f"端口 {port} 起不来：{exc}") from None

    address = f"http://{host}:{port}/"
    print(f"Soroban 工作台已启动：{address}")
    print("按 Ctrl+C 停止")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(address)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
