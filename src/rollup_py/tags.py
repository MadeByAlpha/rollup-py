"""Choosing the bundle's wheel tag from the tags of the wheels it vendors.

A bundle is only installable where *every* vendored wheel is, so the chosen tag has to be at least
as strict as each of them: the same CPython ABI as any version-specific extension, the newest
glibc/musl/macOS floor, and the same architecture.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from packaging.tags import Tag

from rollup_py.errors import RollupError

_LEGACY_MANYLINUX = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
_LEGACY_ALIAS = {version: name for name, version in _LEGACY_MANYLINUX.items()}
_VERSIONED = re.compile(r"^(manylinux|musllinux|macosx)_(\d+)_(\d+)_(.+)$")
_LEGACY = re.compile(r"^(manylinux1|manylinux2010|manylinux2014)_(.+)$")
_CPYTHON = re.compile(r"^cp(\d)(\d+)$")


@dataclass(frozen=True, slots=True, order=True)
class Platform:
    family: str
    version: tuple[int, int]
    arch: str

    @classmethod
    def parse(cls, value: str) -> Platform:
        if match := _VERSIONED.match(value):
            family, major, minor, arch = match.groups()
            return cls(family, (int(major), int(minor)), arch)
        if match := _LEGACY.match(value):
            name, arch = match.groups()
            return cls("manylinux", _LEGACY_MANYLINUX[name], arch)
        if value.startswith("linux_"):
            return cls("linux", (0, 0), value.removeprefix("linux_"))
        # Windows, "any" and anything unknown only ever match themselves.
        return cls(value, (0, 0), "")

    def satisfies(self, other: Platform) -> bool:
        """Whether an environment accepting this platform tag can install a wheel tagged `other`."""
        if other.family == "any":
            return True
        if self.family == "linux":
            # A plain `linux_*` bundle is a local build: accept any Linux wheel of the same architecture.
            return other.family in ("linux", "manylinux", "musllinux") and other.arch == self.arch
        if self.family != other.family:
            return False
        if self.family == "macosx":
            arch_ok = other.arch == self.arch or (
                other.arch == "universal2" and self.arch in ("x86_64", "arm64")
            )
            return arch_ok and self.version >= other.version
        return self.arch == other.arch and self.version >= other.version

    def render(self) -> str:
        if self.family in ("manylinux", "musllinux", "macosx"):
            major, minor = self.version
            rendered = f"{self.family}_{major}_{minor}_{self.arch}"
            if self.family == "manylinux" and (legacy := _LEGACY_ALIAS.get(self.version)):
                rendered += f".{legacy}_{self.arch}"
            return rendered
        if self.family == "linux":
            return f"linux_{self.arch}"
        return self.family

    @property
    def looseness(self) -> tuple[int, tuple[int, int]]:
        # Prefer portable families over `linux_*`, then the lowest version floor.
        return (1 if self.family == "linux" else 0, self.version)


@dataclass(frozen=True, slots=True)
class Abi:
    interpreter: str
    abi: str

    def satisfies(self, other: Abi) -> bool:
        if other.abi == "none":
            if other.interpreter.startswith("py"):
                return self.interpreter.startswith(("py", "cp"))
            return other.interpreter == self.interpreter
        if other.abi == "abi3":
            ours, theirs = _cpython(self.interpreter), _cpython(other.interpreter)
            return (
                ours is not None
                and theirs is not None
                and self.abi in ("abi3", self.interpreter)
                and ours >= theirs
            )
        return self == other

    @property
    def looseness(self) -> tuple[int, tuple[int, int]]:
        if self.abi == "none":
            return (0, (0, 0))
        if self.abi == "abi3":
            return (1, _cpython(self.interpreter) or (0, 0))
        return (2, _cpython(self.interpreter) or (0, 0))


def _cpython(interpreter: str) -> tuple[int, int] | None:
    match = _CPYTHON.match(interpreter)
    return (int(match[1]), int(match[2])) if match else None


def bundle_tag(wheels: Sequence[Iterable[Tag]]) -> str | None:
    """The loosest tag that still implies every non-pure wheel in `wheels`, or `None` if all are pure."""
    constraints = [list(tags) for tags in wheels]
    constraints = [
        tags for tags in constraints if not all(t.abi == "none" and t.platform == "any" for t in tags)
    ]
    if not constraints:
        return None

    abis = {Abi(tag.interpreter, tag.abi) for tags in constraints for tag in tags}
    platforms = {Platform.parse(tag.platform) for tags in constraints for tag in tags}
    # A newer floor within a family may be the only tag satisfying all wheels at once.
    for family, arch in {(p.family, p.arch) for p in platforms}:
        same = [p for p in platforms if (p.family, p.arch) == (family, arch)]
        platforms.add(max(same))

    for abi in sorted(abis, key=lambda a: a.looseness):
        for platform in sorted(platforms, key=lambda p: p.looseness):
            if all(
                any(
                    abi.satisfies(Abi(tag.interpreter, tag.abi))
                    and platform.satisfies(Platform.parse(tag.platform))
                    for tag in tags
                )
                for tags in constraints
            ):
                interpreter = (
                    "py3" if abi.abi == "none" and abi.interpreter.startswith("py") else abi.interpreter
                )
                return f"{interpreter}-{abi.abi}-{platform.render()}"

    listing = "; ".join(".".join(sorted(str(tag) for tag in tags)) for tags in constraints)
    raise RollupError(f"The vendored wheels have no platform in common: {listing}")
