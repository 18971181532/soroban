#!/usr/bin/env python3
"""开发入口：不安装也能直接跑，``python run.py --help``。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from soroban.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
