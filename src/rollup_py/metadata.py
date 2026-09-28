"""Core-metadata edits for the bundle: new name, vendored requirements removed, hoisted ones added."""

from __future__ import annotations

from collections.abc import Collection, Iterable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from rollup_py.lock import Hoisted
from rollup_py.markers import split_extras
from rollup_py.stage import InstalledDist


def rewrite_core_metadata(text: str, *, name: str, drop: Collection[str]) -> str:
    """Rename the distribution and remove `Requires-Dist` entries for vendored packages.

    Only the header block is touched; the long description after the first blank line is kept
    byte for byte. Duplicate requirements (a hoisted one equal to a declared one) are collapsed.
    """
    header, separator, body = text.partition("\n\n")
    kept: list[str] = []
    seen_requirements: set[str] = set()
    skipping = False
    for line in header.splitlines():
        if line[:1] in (" ", "\t"):
            # Continuation of the previous field.
            if not skipping:
                kept.append(line)
            continue
        skipping = False
        key, _, value = line.partition(":")
        value = value.strip()
        if key == "Name":
            line = f"Name: {name}"
        elif key == "Requires-Dist":
            if _requirement_name(value) in drop or value in seen_requirements:
                skipping = True
                continue
            seen_requirements.add(value)
        kept.append(line)
    return "\n".join(kept) + (separator + body if separator else "\n")


def _requirement_name(value: str) -> str | None:
    try:
        return canonicalize_name(Requirement(value).name)
    except InvalidRequirement:
        return None


def hoisted_requirements(hoisted: Iterable[Hoisted], dists: dict[str, InstalledDist]) -> list[str]:
    """Requirement strings the bundle needs because vendored packages depend on external ones.

    The specifier comes from the vendored parent's own metadata (the lock only records the
    resolved version). `extra == ...` clauses are dropped since the parent's extras were already
    decided when it was vendored; environment markers are kept.
    """
    result: list[str] = []
    for item in hoisted:
        parent = item.parent
        dist = dists.get(parent.name)
        declared = (
            parent.requires_dist if parent.requires_dist is not None else (dist.requires_dist if dist else [])
        )
        declared = [
            *declared,
            *(req for group in sorted(item.parent_groups) for req in parent.requires_dev.get(group, ())),
        ]
        matches = [req for req in declared if canonicalize_name(req.name) == item.dependency.name]

        rendered: list[str] = []
        for req in matches:
            env_marker, extras = split_extras(req.marker)
            if extras and not extras & item.parent_extras:
                continue
            text = req.name
            if req.extras:
                text += f"[{','.join(sorted(req.extras))}]"
            text += str(req.specifier)
            if req.url:
                text += f" @ {req.url}"
            if env_marker is not None:
                text += f"{' ' if req.url else ''}; {env_marker}"
            rendered.append(text)

        if not rendered:
            # Nothing usable in the metadata (unusual): fall back to the lock's view of the edge.
            text = item.dependency.name
            if item.dependency.extras:
                text += f"[{','.join(sorted(item.dependency.extras))}]"
            if item.dependency.marker:
                text += f"; {item.dependency.marker}"
            rendered.append(text)

        for text in rendered:
            if text not in result:
                result.append(text)
    return result
