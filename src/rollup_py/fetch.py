"""Installing the exact locked distributions into a staging directory."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

from rollup_py.errors import RollupError
from rollup_py.lock import LockedPackage, Lockfile
from rollup_py.uvcli import run_uv


def fetch(
    lock: Lockfile,
    packages: list[LockedPackage],
    target: Path,
    *,
    python_version: str | None,
    python_platform: str | None,
) -> None:
    """`uv pip install --target` every package without resolving anything again.

    Registry and direct-URL packages are pinned with every hash recorded in the lock; git and
    local packages are pinned by commit or path.
    """
    hashed: defaultdict[str | None, list[str]] = defaultdict(list)
    unhashed: list[str] = []

    for package in packages:
        kind = package.source_kind
        value = package.source[kind]
        if kind == "registry":
            hashed[value].append(_hashed_line(f"{package.name}=={package.version}", package))
        elif kind == "url":
            hashed[None].append(_hashed_line(f"{package.name} @ {value}", package))
        elif kind == "git":
            unhashed.append(f"{package.name} @ {_git_url(value)}")
        elif kind in ("editable", "directory", "path"):
            unhashed.append(f"{package.name} @ {lock.local_path(package).as_uri()}")
        elif kind == "virtual":
            # Virtual workspace members have no build system and therefore no files of their own.
            continue
        else:
            raise RollupError(f"`{package.name}` has an unsupported source in {lock.path}: {package.source}")

    base = ["pip", "install", "--target", str(target), "--no-deps", "--python", sys.executable]
    if python_version:
        base += ["--python-version", python_version]
    if python_platform:
        base += ["--python-platform", python_platform]

    for index, lines in sorted(hashed.items(), key=lambda item: item[0] or ""):
        args = [*base, "--require-hashes", *_index_args(index, lock.root)]
        _install(args, lines, target, lock.root)
    if unhashed:
        _install(base, unhashed, target, lock.root)


def _hashed_line(requirement: str, package: LockedPackage) -> str:
    hashes = package.hashes
    if not hashes:
        raise RollupError(f"`{package.name}` has no hashes in uv.lock; run `uv lock` again")
    return " ".join([requirement, *(f"--hash={value}" for value in hashes)])


def _index_args(index: str | None, root: Path) -> list[str]:
    if index is None:
        return []
    if "://" in index:
        return ["--index-url", index]
    # A registry given as a path is a flat directory of distributions.
    return ["--no-index", "--find-links", str((root / index).resolve())]


def _git_url(value: str) -> str:
    """`https://host/repo?subdirectory=x&rev=main#<commit>` -> `git+https://host/repo@<commit>#subdirectory=x`."""
    parts = urlsplit(value)
    commit = parts.fragment
    if not commit:
        raise RollupError(f"git source {value!r} in uv.lock has no pinned commit")
    url = f"git+{urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))}@{commit}"
    if subdirectory := parse_qs(parts.query).get("subdirectory"):
        url += f"#subdirectory={subdirectory[0]}"
    return url


def _install(args: list[str], lines: list[str], target: Path, cwd: Path) -> None:
    requirements = target.parent / f"requirements-{len(list(target.parent.glob('requirements-*.txt')))}.txt"
    requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_uv([*args, "-r", str(requirements)], cwd=cwd)
