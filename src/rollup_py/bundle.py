"""Everything the `rollup` target needs, computed before hatchling writes the wheel."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.utils import NormalizedName

from rollup_py.config import RollupConfig
from rollup_py.fetch import fetch
from rollup_py.lock import Lockfile, VendorPlan, find_lockfile, plan_vendoring
from rollup_py.markers import target_environment
from rollup_py.metadata import hoisted_requirements
from rollup_py.stage import InstalledDist, collect, merge
from rollup_py.tags import bundle_tag
from rollup_py.uvcli import run_uv

MANIFEST_NAME = "rollup.json"
VENDOR_METADATA_DIR = "vendor"


@dataclass(slots=True)
class Log:
    info: Callable[[str], None]
    warning: Callable[[str], None]


@dataclass(slots=True)
class Bundle:
    plan: VendorPlan
    dists: list[InstalledDist]
    # Wheel path -> source file, for everything vendored.
    files: dict[str, Path]
    # Path under `<dist-info>/` -> source file (vendored METADATA and license files).
    metadata_files: dict[str, Path]
    hoisted: list[str]
    tag: str | None

    @property
    def vendored_names(self) -> frozenset[NormalizedName]:
        return self.plan.vendored_names

    def manifest(self) -> str:
        vendored: list[dict[str, Any]] = [
            {
                "name": dist.display_name,
                "version": dist.version,
                "source": _source_of(self.plan, dist),
                "top_level": dist.top_level,
                "pure": dist.is_pure,
            }
            for dist in self.dists
        ]
        return json.dumps({"vendored": vendored, "requires": self.hoisted}, indent=2, sort_keys=True) + "\n"


def _source_of(plan: VendorPlan, dist: InstalledDist) -> dict[str, str]:
    for package in plan.packages:
        if package.name == dist.name:
            return package.source
    return {}


def build_bundle(
    *,
    project_root: Path,
    project_name: str,
    config: RollupConfig,
    stage_dir: Path,
    reserved: Callable[[str], str | None],
    log: Log,
) -> Bundle:
    lock_path = find_lockfile(project_root, config.lock)
    if config.check_lock:
        # `--check` fails when the lock no longer matches the workspace's declared requirements.
        run_uv(["lock", "--check"], cwd=lock_path.parent)
    lock = Lockfile.load(lock_path)

    environment = target_environment(config.python_version, config.python_platform)
    plan = plan_vendoring(lock, project_name, config, environment)
    if not plan.vendored:
        log.warning("rollup: nothing to vendor; the bundle only differs from the standard wheel by its name")
    for extra, deps in sorted(plan.project.optional_dependencies.items()):
        if shadowed := sorted({dep.name for dep in deps} & plan.vendored_names):
            log.warning(
                f"rollup: extra `{extra}` requires vendored {', '.join(shadowed)}; "
                "dropping those requirements from the bundle"
            )

    target = stage_dir / "site"
    target.mkdir(parents=True)
    fetch(
        lock,
        plan.packages,
        target,
        python_version=config.python_version,
        python_platform=config.python_platform,
    )
    dists = collect(target, plan.packages)

    files = merge(dists, reserved)
    metadata_files: dict[str, Path] = {}
    for dist in dists:
        prefix = f"{VENDOR_METADATA_DIR}/{dist.dist_info.name.removesuffix('.dist-info')}"
        for relative, source in dist.license_files().items():
            metadata_files[f"{prefix}/{relative}"] = source
        if dist.dropped:
            log.warning(f"rollup: {dist.name}: not bundling scripts/data files: {', '.join(dist.dropped)}")
        if dist.uses_own_metadata():
            log.warning(
                f"rollup: {dist.name} uses importlib.metadata or pkg_resources; lookups of its own "
                "distribution metadata will fail because vendored packages have no dist-info"
            )

    hoisted = hoisted_requirements(plan.hoisted, {dist.name: dist for dist in dists})
    tag = bundle_tag([dist.tags for dist in dists])

    names = ", ".join(f"{dist.name}=={dist.version}" for dist in dists) or "nothing"
    log.info(f"rollup: vendoring {names}")
    return Bundle(
        plan=plan, dists=dists, files=files, metadata_files=metadata_files, hoisted=hoisted, tag=tag
    )
