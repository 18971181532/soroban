#!/usr/bin/env python3
"""跑全部测试：``python run_tests.py``，加 ``-v`` 看每条用例。"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    start = time.perf_counter()
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(os.path.dirname(__file__), "tests"),
                            top_level_dir=os.path.dirname(__file__))
    verbosity = 2 if "-v" in sys.argv else 1
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    elapsed = time.perf_counter() - start
    total = result.testsRun
    print()
    print(f"{total} 个测试 · {elapsed:.2f}s · "
          f"{len(result.failures)} 失败 · {len(result.errors)} 错误")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
