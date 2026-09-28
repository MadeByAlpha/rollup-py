"""Environment-marker helpers: settling `extra` clauses and building target environments."""

from __future__ import annotations

from collections.abc import Collection
from typing import cast

from packaging._parser import Node, Variable
from packaging.markers import Marker, default_environment
from packaging.utils import canonicalize_name


def split_extras(marker: Marker | None) -> tuple[Marker | None, frozenset[str]]:
    """Return `marker` without its `extra` comparisons, plus the (normalized) extras it referenced.

    Requirements in core metadata gate optional dependencies with `extra == "name"`. Once a
    requirement is hoisted out of a vendored package into the bundle's own metadata the extra no
    longer means anything, so only the environment part of the marker is kept.
    """
    if marker is None:
        return None, frozenset()

    extras: set[str] = set()
    stripped = _strip(marker._markers, extras)
    if stripped is None:
        return None, frozenset(extras)
    return Marker(_format(stripped)), frozenset(extras)


def _strip(node: object, extras: set[str]) -> object | None:
    if isinstance(node, tuple):
        lhs, _, rhs = cast(tuple[Node, Node, Node], node)
        if isinstance(lhs, Variable) and lhs.value == "extra":
            extras.add(canonicalize_name(rhs.value))
            return None
        if isinstance(rhs, Variable) and rhs.value == "extra":
            extras.add(canonicalize_name(lhs.value))
            return None
        return cast(object, node)

    # A list alternates between operands and the "and"/"or" joining them.
    items = cast(list[object], node)
    kept: list[object] = []
    for index in range(0, len(items), 2):
        operand = _strip(items[index], extras)
        if operand is None:
            continue
        if kept:
            kept.append(items[index - 1])
        kept.append(operand)
    return kept or None


def evaluate_extras(marker: Marker, active: Collection[str]) -> Marker | bool:
    """Decide every `extra` comparison in `marker`, taking `active` as the set of enabled extras.

    uv writes `[tool.uv] conflicts` into `uv.lock` as `extra == 'extra-3-app-prod'` style clauses
    (see `conflict_item`). Unlike environment markers they are known when the bundle is built, so
    they are settled here. Returns a bool when nothing else is left, else the remaining marker.
    """
    result = _reduce(marker._markers, frozenset(canonicalize_name(name) for name in active))
    if isinstance(result, bool):
        return result
    return Marker(_format(result))


def conflict_item(kind: str, package: str, name: str) -> str:
    """The name uv gives an extra or dependency group in the conflict markers of `uv.lock`."""
    package = canonicalize_name(package)
    return f"{kind}-{len(package)}-{package}-{canonicalize_name(name)}"


def _reduce(node: object, active: frozenset[str]) -> object:
    if isinstance(node, tuple):
        lhs, op, rhs = cast(tuple[Node, Node, Node], node)
        if isinstance(lhs, Variable) and lhs.value == "extra":
            value = rhs.value
        elif isinstance(rhs, Variable) and rhs.value == "extra":
            value = lhs.value
        else:
            return cast(object, node)
        if op.value == "==":
            return canonicalize_name(value) in active
        if op.value == "!=":
            return canonicalize_name(value) not in active
        return cast(object, node)

    # "and" binds tighter than "or": split into or-groups of and-ed operands, as packaging does.
    items = cast(list[object], node)
    groups: list[list[object]] = [[]]
    for index in range(0, len(items), 2):
        if index and items[index - 1] == "or":
            groups.append([])
        groups[-1].append(_reduce(items[index], active))

    kept: list[object] = []
    for group in groups:
        if any(operand is False for operand in group):
            continue
        rest = [operand for operand in group if operand is not True]
        if not rest:
            return True
        if kept:
            kept.append("or")
        kept.append(rest[0] if len(rest) == 1 else _join(rest, "and"))
    if not kept:
        return False
    return kept[0] if len(kept) == 1 else kept


def _join(operands: list[object], operator: str) -> list[object]:
    joined: list[object] = []
    for operand in operands:
        if joined:
            joined.append(operator)
        joined.append(operand)
    return joined


def _format(node: object, *, top: bool = True) -> str:
    if isinstance(node, tuple):
        return " ".join(part.serialize() for part in cast(tuple[Node, Node, Node], node))
    if isinstance(node, str):
        return node
    items = cast(list[object], node)
    inner = " ".join(_format(part, top=False) for part in items)
    return inner if top or len(items) == 1 else f"({inner})"


def target_environment(python_version: str | None, python_platform: str | None) -> dict[str, str]:
    """Marker environment of the interpreter the bundle targets.

    Defaults to the running interpreter, overridden by the same `--python-version` and
    `--python-platform` values that are forwarded to `uv pip install`.
    """
    env = {key: str(value) for key, value in default_environment().items()}
    if python_version:
        parts = python_version.split(".")
        env["python_version"] = ".".join(parts[:2])
        env["python_full_version"] = python_version if len(parts) >= 3 else f"{python_version}.0"
    if python_platform:
        env.update(_platform_environment(python_platform.lower()))
    return env


def _platform_environment(triple: str) -> dict[str, str]:
    # uv accepts bare names ("linux", "windows", "macos") as well as triples ("aarch64-apple-darwin").
    arch = triple.split("-", 1)[0] if "-" in triple else None
    env: dict[str, str]
    if "windows" in triple:
        env = {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"}
        machine = {"x86_64": "AMD64", "aarch64": "ARM64", "i686": "x86"}.get(arch or "", arch)
    elif "macos" in triple or "darwin" in triple or "apple" in triple:
        env = {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix"}
        machine = {"aarch64": "arm64"}.get(arch or "", arch)
    elif "linux" in triple:
        env = {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"}
        machine = arch
    else:
        return {}
    if machine:
        env["platform_machine"] = machine
    return env
