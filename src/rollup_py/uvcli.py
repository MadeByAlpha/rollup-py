"""Locating and running the uv executable."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from rollup_py.errors import RollupError


def find_uv() -> str:
    # uv exports its own path as `UV` to everything it runs (`uv run`, `uvx`, build backends).
    if (from_env := os.environ.get("UV")) and Path(from_env).is_file():
        return from_env
    if on_path := shutil.which("uv"):
        return on_path
    try:
        from uv import find_uv_bin  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
    except ImportError:
        pass
    else:
        return str(find_uv_bin())  # pyright: ignore[reportUnknownArgumentType]
    raise RollupError("uv was not found; install it (https://docs.astral.sh/uv/) or run the build through uv")


def run_uv(args: list[str], *, cwd: Path) -> str:
    command = [find_uv(), *args]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip()
        raise RollupError(f"`uv {' '.join(args)}` failed (exit code {result.returncode}):\n{output}")
    return result.stdout
