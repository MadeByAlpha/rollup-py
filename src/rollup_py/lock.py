"""Reading `uv.lock` and deciding what gets vendored."""

from __future__ import annotations

import tomllib
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import NormalizedName, canonicalize_name

from rollup_py.config import TABLE, RollupConfig
from rollup_py.errors import RollupError

LOCK_FILENAME = "uv.lock"
LOCAL_SOURCES = frozenset({"editable", "virtual", "directory"})


@dataclass(frozen=True, slots=True)
class Dependency:
    """An edge in the lock graph."""

    name: NormalizedName
    extras: frozenset[str] = frozenset()
    marker: str | None = None
    version: str | None = None
    source: tuple[tuple[str, str], ...] | None = None

    @classmethod
    def from_toml(cls, data: Mapping[str, Any]) -> Dependency:
        source = data.get("source")
        return cls(
            name=canonicalize_name(data["name"]),
            extras=frozenset(canonicalize_name(extra) for extra in data.get("extra", ())),
            marker=data.get("marker"),
            version=data.get("version"),
            source=_source_key(source) if source is not None else None,
        )


@dataclass(slots=True)
class LockedPackage:
    name: NormalizedName
    version: str | None
    source: dict[str, str]
    dependencies: list[Dependency]
    optional_dependencies: dict[str, list[Dependency]]
    sdist: dict[str, Any] | None
    wheels: list[dict[str, Any]]
    # Only recorded for local (workspace, path) packages.
    requires_dist: list[Requirement] | None

    @property
    def key(self) -> tuple[str, str | None, tuple[tuple[str, str], ...]]:
        return (self.name, self.version, _source_key(self.source))

    @property
    def source_kind(self) -> str:
        return next(iter(self.source))

    @property
    def is_local(self) -> bool:
        return self.source_kind in LOCAL_SOURCES

    @property
    def hashes(self) -> list[str]:
        artifacts = [*self.wheels, *([self.sdist] if self.sdist else [])]
        return sorted({artifact["hash"] for artifact in artifacts if "hash" in artifact})

    def edges(self, extras: Iterable[str] = ()) -> list[Dependency]:
        edges = list(self.dependencies)
        for extra in extras:
            edges.extend(self.optional_dependencies.get(extra, ()))
        return edges


@dataclass(slots=True)
class Lockfile:
    path: Path
    packages: list[LockedPackage]

    @property
    def root(self) -> Path:
        return self.path.parent

    @classmethod
    def load(cls, path: Path) -> Lockfile:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise RollupError(f"Cannot read {path}: {exc}") from exc

        if data.get("version") != 1:
            raise RollupError(f"{path}: unsupported lockfile version {data.get('version')!r} (expected 1)")

        packages = [_parse_package(raw) for raw in data.get("package", [])]
        return cls(path=path, packages=packages)

    def local_package(self, name: str) -> LockedPackage:
        normalized = canonicalize_name(name)
        for package in self.packages:
            if package.name == normalized and package.is_local:
                return package
        raise RollupError(f"`{name}` is not a workspace member or local package in {self.path}")

    def local_path(self, package: LockedPackage) -> Path:
        return (self.root / next(iter(package.source.values()))).resolve()

    def resolve(self, dependency: Dependency) -> LockedPackage:
        candidates = [package for package in self.packages if package.name == dependency.name]
        if dependency.version is not None:
            candidates = [package for package in candidates if package.version == dependency.version]
        if dependency.source is not None:
            candidates = [
                package for package in candidates if _source_key(package.source) == dependency.source
            ]
        if len(candidates) != 1:
            raise RollupError(
                f"{self.path}: cannot resolve `{dependency.name}` "
                f"({len(candidates)} matching packages); try `uv lock` again"
            )
        return candidates[0]


def find_lockfile(start: Path, configured: str | None) -> Path:
    if configured is not None:
        path = (start / configured).resolve()
        if not path.is_file():
            raise RollupError(f"`{TABLE}.lock` points to {path}, which does not exist")
        return path

    for directory in (start, *start.parents):
        candidate = directory / LOCK_FILENAME
        if candidate.is_file():
            return candidate
    raise RollupError(f"No {LOCK_FILENAME} found in {start} or its parents; run `uv lock` first")


def _parse_package(raw: Mapping[str, Any]) -> LockedPackage:
    metadata = raw.get("metadata")
    requires_dist = None
    if metadata is not None:
        requires_dist = [_requirement_from_lock(entry) for entry in metadata.get("requires-dist", [])]
    return LockedPackage(
        name=canonicalize_name(raw["name"]),
        version=raw.get("version"),
        source=dict(raw["source"]),
        dependencies=[Dependency.from_toml(dep) for dep in raw.get("dependencies", [])],
        optional_dependencies={
            canonicalize_name(extra): [Dependency.from_toml(dep) for dep in deps]
            for extra, deps in raw.get("optional-dependencies", {}).items()
        },
        sdist=raw.get("sdist"),
        wheels=list(raw.get("wheels", [])),
        requires_dist=requires_dist,
    )


def _requirement_from_lock(entry: Mapping[str, Any]) -> Requirement:
    text = entry["name"]
    if extras := entry.get("extras"):
        text += f"[{','.join(extras)}]"
    text += entry.get("specifier", "")
    if marker := entry.get("marker"):
        text += f"; {marker}"
    return Requirement(text)


def _source_key(source: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in source.items()))


@dataclass(slots=True)
class Hoisted:
    """A dependency of a vendored package that stays an ordinary requirement of the bundle."""

    parent: LockedPackage
    dependency: Dependency
    # The parent's activated extras; they decide which of its requirements apply.
    parent_extras: frozenset[str]


@dataclass(slots=True)
class VendorPlan:
    project: LockedPackage
    # Vendored packages with the union of extras requested on them.
    vendored: dict[tuple[str, str | None, tuple[tuple[str, str], ...]], tuple[LockedPackage, set[str]]] = (
        field(
            default_factory=dict[
                tuple[str, str | None, tuple[tuple[str, str], ...]], tuple[LockedPackage, set[str]]
            ]
        )
    )
    hoisted: list[Hoisted] = field(default_factory=list[Hoisted])

    @property
    def vendored_names(self) -> frozenset[NormalizedName]:
        return frozenset(package.name for package, _ in self.vendored.values())

    @property
    def packages(self) -> list[LockedPackage]:
        return [package for package, _ in self.vendored.values()]


def plan_vendoring(
    lock: Lockfile, project_name: str, config: RollupConfig, environment: Mapping[str, str]
) -> VendorPlan:
    """Walk the lock graph from the project and split it into vendored and external packages.

    Direct dependencies that are not vendored stay in the bundle's metadata as written. A walk
    stops at every package that is not vendored: its own dependencies are the installer's job.
    """
    project = lock.local_package(project_name)
    plan = VendorPlan(project=project)

    direct_names = {dep.name for dep in project.dependencies}
    if config.vendor is not None and (unknown := config.vendor - direct_names):
        raise RollupError(
            f"`{TABLE}.vendor` lists packages that are not direct dependencies of `{project.name}`: "
            f"{', '.join(sorted(unknown))}"
        )

    queue: deque[tuple[LockedPackage | None, frozenset[str], Dependency]] = deque()
    for dep in project.dependencies:
        if config.vendor is None or dep.name in config.vendor:
            queue.append((None, frozenset(), dep))

    def vendor(dep: Dependency) -> None:
        package = lock.resolve(dep)
        known = plan.vendored.get(package.key)
        requested = set(dep.extras)
        if known is None:
            plan.vendored[package.key] = (package, requested)
            new_extras = requested
            queue.extend((package, frozenset(requested), edge) for edge in package.edges())
        else:
            new_extras = requested - known[1]
            known[1].update(new_extras)
        all_extras = frozenset(plan.vendored[package.key][1])
        for extra in sorted(new_extras):
            queue.extend((package, all_extras, edge) for edge in package.optional_dependencies.get(extra, ()))

    while queue:
        parent, parent_extras, dep = queue.popleft()
        if _should_vendor(dep, config, environment, direct=parent is None):
            vendor(dep)
        elif parent is not None and _applies(dep, config, environment):
            plan.hoisted.append(Hoisted(parent, dep, parent_extras))

        if not queue:
            # A hoisted edge to a package that another path vendors is served by the vendored copy, so
            # it is no requirement of the bundle. The copy still has to carry the extras the edge asks
            # for (possibly queueing more edges), and a different locked version is a clash.
            vendored = plan.vendored_names
            for item in plan.hoisted:
                if item.dependency.name in vendored:
                    vendor(item.dependency)
            plan.hoisted = [item for item in plan.hoisted if item.dependency.name not in vendored]

    _check_version_clashes(plan)
    _check_external_overlap(lock, plan, config, environment)
    return plan


def _should_vendor(
    dep: Dependency, config: RollupConfig, environment: Mapping[str, str], *, direct: bool
) -> bool:
    if dep.name in config.external:
        return False
    if not direct and not config.transitive:
        return False
    if dep.marker is None:
        return True
    if config.conditional == "external":
        return False
    return _evaluate(dep.marker, environment)


def _applies(dep: Dependency, config: RollupConfig, environment: Mapping[str, str]) -> bool:
    # With `conditional = "evaluate"` an edge whose marker is false for the target is simply dropped.
    return dep.marker is None or config.conditional == "external" or _evaluate(dep.marker, environment)


def _evaluate(marker: str, environment: Mapping[str, str]) -> bool:
    return Marker(marker).evaluate({"extra": "", **environment})


def _check_version_clashes(plan: VendorPlan) -> None:
    seen: dict[str, str | None] = {}
    for package in plan.packages:
        if package.name in seen:
            raise RollupError(
                f"Two versions of `{package.name}` would be vendored ({seen[package.name]} and "
                f'{package.version}); use `conditional = "evaluate"` or mark it external'
            )
        seen[package.name] = package.version


def _check_external_overlap(
    lock: Lockfile, plan: VendorPlan, config: RollupConfig, environment: Mapping[str, str]
) -> None:
    """Fail if an installer would also install a vendored package as a separate distribution.

    Vendored packages keep their import names, so a real copy installed next to the bundle would
    overwrite (and later uninstall) the same files.
    """
    vendored = plan.vendored_names
    starts: list[tuple[Dependency, str]] = [
        (dep, f"{plan.project.name} -> {dep.name}") for dep in plan.project.dependencies
    ]
    starts.extend(
        (dep, f"{plan.project.name}[{extra}] -> {dep.name}")
        for extra, deps in plan.project.optional_dependencies.items()
        for dep in deps
    )
    starts.extend((item.dependency, f"{item.parent.name} -> {item.dependency.name}") for item in plan.hoisted)

    visited: set[tuple[str, str | None, tuple[tuple[str, str], ...], frozenset[str]]] = set()
    # Only packages the installer brings in are walked; a vendored one is part of the bundle itself.
    queue = deque((dep, path) for dep, path in starts if dep.name not in vendored)
    while queue:
        dep, path = queue.popleft()
        if (
            dep.marker is not None
            and config.conditional == "evaluate"
            and not _evaluate(dep.marker, environment)
        ):
            continue
        package = lock.resolve(dep)
        state = (*package.key, dep.extras)
        if state in visited:
            continue
        visited.add(state)
        for edge in package.edges(dep.extras):
            if edge.name in vendored:
                raise RollupError(
                    f"`{edge.name}` is vendored but is also required by an external package "
                    f"({path} -> {edge.name}); vendor that package too or add `{edge.name}` to `external`"
                )
            queue.append((edge, f"{path} -> {edge.name}"))
