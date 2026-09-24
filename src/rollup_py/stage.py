"""Turning `uv pip install --target` output into files for the bundle."""

from __future__ import annotations

import csv
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from email.parser import HeaderParser
from pathlib import Path, PurePosixPath

from packaging.requirements import Requirement
from packaging.tags import Tag, parse_tag
from packaging.utils import NormalizedName, canonicalize_name

from rollup_py.errors import RollupError
from rollup_py.lock import LockedPackage

# Top-level suffixes that belong in site-packages; anything else at the root is data that happens to
# have been installed next to the code (READMEs, license files dropped by old setup.py scripts...).
ROOT_SUFFIXES = (".py", ".pyi", ".so", ".pyd", ".pth")
# Install-scheme directories `uv pip install --target` creates for scripts, headers and data files.
SCHEME_DIRECTORIES = frozenset({"bin", "Scripts", "include", "share", "etc", "man"})
LICENSE_PATTERN = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)
METADATA_USAGE = re.compile(r"\b(importlib\.metadata|importlib_metadata|pkg_resources)\b")


@dataclass(slots=True)
class InstalledDist:
    name: NormalizedName
    display_name: str
    version: str
    dist_info: Path
    metadata: Message
    tags: list[Tag]
    root_is_purelib: bool
    # Wheel-relative POSIX path -> installed file.
    files: dict[str, Path] = field(default_factory=dict[str, Path])
    dropped: list[str] = field(default_factory=list[str])

    @property
    def is_pure(self) -> bool:
        return self.root_is_purelib and all(tag.abi == "none" and tag.platform == "any" for tag in self.tags)

    @property
    def top_level(self) -> list[str]:
        return sorted({PurePosixPath(path).parts[0] for path in self.files})

    @property
    def requires_dist(self) -> list[Requirement]:
        return [Requirement(value) for value in self.metadata.get_all("Requires-Dist", [])]

    def license_files(self) -> dict[str, Path]:
        """Files worth shipping next to the vendored code: its METADATA and anything license-like."""
        found = {"METADATA": self.dist_info / "METADATA"}
        for path in sorted(self.dist_info.rglob("*")):
            relative = path.relative_to(self.dist_info).as_posix()
            if path.is_file() and (relative.startswith("licenses/") or LICENSE_PATTERN.match(path.name)):
                found[relative] = path
        return found

    def uses_own_metadata(self) -> bool:
        return any(
            METADATA_USAGE.search(path.read_text(encoding="utf-8", errors="replace"))
            for relative, path in self.files.items()
            if relative.endswith(".py")
        )


def collect(target: Path, expected: list[LockedPackage]) -> list[InstalledDist]:
    """Read every installed `*.dist-info` and keep the files that should land in the bundle."""
    wanted = {package.name: package for package in expected if package.source_kind != "virtual"}
    dists: list[InstalledDist] = []
    for dist_info in sorted(target.glob("*.dist-info")):
        dist = _read_dist(dist_info)
        if dist.name not in wanted:
            raise RollupError(f"Unexpected distribution `{dist.name}` was installed while staging")
        dists.append(dist)

    missing = set(wanted) - {dist.name for dist in dists}
    if missing:
        raise RollupError(f"uv did not install: {', '.join(sorted(missing))}")
    return dists


def _read_dist(dist_info: Path) -> InstalledDist:
    metadata = HeaderParser().parsestr((dist_info / "METADATA").read_text(encoding="utf-8"))
    wheel = HeaderParser().parsestr((dist_info / "WHEEL").read_text(encoding="utf-8"))
    tags = [tag for value in wheel.get_all("Tag", []) for tag in parse_tag(value)]
    dist = InstalledDist(
        name=canonicalize_name(metadata["Name"]),
        display_name=metadata["Name"],
        version=metadata["Version"],
        dist_info=dist_info,
        metadata=metadata,
        tags=tags,
        root_is_purelib=wheel.get("Root-Is-Purelib", "true").strip().lower() == "true",
    )

    root = dist_info.parent
    with (dist_info / "RECORD").open(encoding="utf-8", newline="") as record:
        paths = [row[0] for row in csv.reader(record) if row]

    importable_roots = {
        PurePosixPath(path).parts[0]
        for path in paths
        if len(PurePosixPath(path).parts) > 1 and path.endswith((".py", ".so", ".pyd"))
    }
    for path in paths:
        parts = PurePosixPath(path).parts
        if not parts or parts[0] == dist_info.name or parts[0] == ".." or "__pycache__" in parts:
            continue
        if path.endswith(".pyc"):
            continue
        is_file_at_root = len(parts) == 1
        if (is_file_at_root and not path.endswith(ROOT_SUFFIXES)) or (
            parts[0] in SCHEME_DIRECTORIES and parts[0] not in importable_roots
        ):
            dist.dropped.append(path)
            continue
        dist.files[path] = root / path
    return dist


def merge(dists: list[InstalledDist], reserved: Callable[[str], str | None]) -> dict[str, Path]:
    """Combine every dist's files, refusing to let two owners write the same path.

    `reserved` names the owner of a path that is already taken by the project itself (or `None`).
    Directories may be shared, which is how namespace packages end up merged.
    """
    merged: dict[str, Path] = {}
    owners: dict[str, str] = {}
    for dist in dists:
        for path, source in dist.files.items():
            owner = owners.get(path) or reserved(path)
            if owner is not None:
                raise RollupError(f"`{path}` is provided by both `{owner}` and vendored `{dist.name}`")
            merged[path] = source
            owners[path] = dist.name
    return dict(sorted(merged.items()))
