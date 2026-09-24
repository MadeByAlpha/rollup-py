"""`rollup-py build`: run the `rollup` target through hatchling, uv-workspace aware."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from rollup_py.config import ENV_PYTHON_PLATFORM, ENV_PYTHON_VERSION, TARGET_NAME
from rollup_py.errors import RollupError
from rollup_py.lock import Lockfile, find_lockfile


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rollup-py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="build the bundled wheel")
    build.add_argument("path", nargs="?", type=Path, help="project directory (default: current directory)")
    build.add_argument("--package", help="workspace member to build, looked up in uv.lock")
    build.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        help="output directory (default: dist/ of the workspace root with --package, else of the project)",
    )
    build.add_argument("--python-version", help="target Python version, forwarded to `uv pip install`")
    build.add_argument("--python-platform", help="target platform, forwarded to `uv pip install`")
    build.add_argument(
        "-c", "--clean", action="store_true", help="remove earlier bundles from the output directory"
    )

    args = parser.parse_args(argv)
    try:
        return _build(args)
    except RollupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build(args: argparse.Namespace) -> int:
    start = (args.path or Path.cwd()).resolve()
    if args.package:
        lock = Lockfile.load(find_lockfile(start, None))
        project = lock.local_path(lock.local_package(args.package))
        out_dir = args.out_dir or lock.root / "dist"
    else:
        project = start
        out_dir = args.out_dir or project / "dist"

    if not (project / "pyproject.toml").is_file():
        raise RollupError(f"{project} has no pyproject.toml")

    if args.python_version:
        os.environ[ENV_PYTHON_VERSION] = args.python_version
    if args.python_platform:
        os.environ[ENV_PYTHON_PLATFORM] = args.python_platform

    from hatchling.cli.build import build_impl

    # hatchling builds the project in the current directory.
    os.chdir(project)
    build_impl(
        called_by_app=False,
        directory=str(Path(out_dir).resolve()),
        targets=[TARGET_NAME],
        hooks_only=False,
        no_hooks=False,
        clean=args.clean,
        clean_hooks_after=False,
        clean_only=False,
        show_dynamic_deps=False,
    )
    return 0
