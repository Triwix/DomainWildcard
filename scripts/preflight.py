#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
OUTPUT_DIR = ROOT_DIR / "output"


def _ok(message: str) -> Tuple[bool, str]:
    return True, f"PASS: {message}"


def _fail(message: str) -> Tuple[bool, str]:
    return False, f"FAIL: {message}"


def check_python_version() -> Tuple[bool, str]:
    if sys.version_info < (3, 9):
        return _fail(f"Python {sys.version.split()[0]} detected; require >= 3.9")
    return _ok(f"Python {sys.version.split()[0]}")


def check_python_imports() -> Tuple[bool, str]:
    modules = ["fastapi", "httpx", "uvicorn", "multipart"]
    missing: List[str] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    if missing:
        return _fail(f"Missing Python modules: {', '.join(missing)}")
    return _ok("Required Python modules import cleanly")


def check_node_available() -> Tuple[bool, str]:
    node_path = shutil.which("node")
    if not node_path:
        return _fail("Node.js not found in PATH")
    return _ok(f"Node.js found at {node_path}")


def check_required_files() -> Tuple[bool, str]:
    required = [
        ROOT_DIR / "README.md",
        APP_DIR / "main.py",
        STATIC_DIR / "index.html",
        STATIC_DIR / "app.js",
        STATIC_DIR / "style.css",
        ROOT_DIR / "scripts" / "domain_batch_run.py",
    ]
    missing = [str(path.relative_to(ROOT_DIR)) for path in required if not path.exists()]
    if missing:
        return _fail(f"Missing required files: {', '.join(missing)}")
    return _ok("Required files present")


def _check_dir_writable(path: Path) -> Tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".preflight-", dir=str(path), delete=False) as handle:
            temp_path = Path(handle.name)
        temp_path.unlink(missing_ok=True)
    except Exception as exc:
        return _fail(f"{path.relative_to(ROOT_DIR)} not writable: {exc}")
    return _ok(f"{path.relative_to(ROOT_DIR)} is writable")


def main() -> int:
    checks = [
        check_python_version,
        check_python_imports,
        check_node_available,
        check_required_files,
        lambda: _check_dir_writable(DATA_DIR),
        lambda: _check_dir_writable(OUTPUT_DIR),
    ]
    failed = 0
    for check in checks:
        ok, line = check()
        print(line)
        if not ok:
            failed += 1

    if failed:
        print(f"Preflight failed: {failed} check(s) failed.")
        return 2

    print("Preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
