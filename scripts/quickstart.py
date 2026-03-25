#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import venv
from pathlib import Path
from typing import Iterable, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT_DIR / ".venv"
REQUIREMENTS_PATH = ROOT_DIR / "requirements.txt"
REQUIREMENTS_STAMP = VENV_DIR / ".requirements.sha256"


def _venv_python_path(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _requirements_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_venv() -> Path:
    python_path = _venv_python_path(VENV_DIR)
    if python_path.exists():
        return python_path
    print("Creating local virtualenv (.venv)...")
    builder = venv.EnvBuilder(with_pip=True)
    builder.create(VENV_DIR)
    return _venv_python_path(VENV_DIR)


def ensure_requirements(venv_python: Path) -> None:
    if not REQUIREMENTS_PATH.exists():
        raise FileNotFoundError(f"Missing requirements file: {REQUIREMENTS_PATH}")
    current_hash = _requirements_hash(REQUIREMENTS_PATH)
    if REQUIREMENTS_STAMP.exists() and REQUIREMENTS_STAMP.read_text(encoding="utf-8").strip() == current_hash:
        return

    print("Installing/updating dependencies...")
    subprocess.check_call([str(venv_python), "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)], cwd=str(ROOT_DIR))
    REQUIREMENTS_STAMP.write_text(current_hash, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quickstart launcher for local web UI.")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the browser.")
    parser.add_argument("--no-install", action="store_true", help="Skip dependency install check.")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args, passthrough = parser.parse_known_args(list(argv) if argv is not None else None)

    venv_python = ensure_venv()
    if not args.no_install:
        ensure_requirements(venv_python)

    launcher_cmd = [str(venv_python), "-m", "app.launcher"]
    if not args.no_open and "--open" not in passthrough:
        launcher_cmd.append("--open")
    launcher_cmd.extend(passthrough)
    return subprocess.call(launcher_cmd, cwd=str(ROOT_DIR))


if __name__ == "__main__":
    raise SystemExit(main())
