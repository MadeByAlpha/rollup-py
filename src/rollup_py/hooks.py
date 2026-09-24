"""Hatch plugin registration (`[project.entry-points.hatch]`)."""

from __future__ import annotations

from hatchling.plugin import hookimpl

from rollup_py.builder import RollupBuilder
from rollup_py.hook import RollupBuildHook


@hookimpl
def hatch_register_builder() -> type[RollupBuilder]:
    return RollupBuilder


@hookimpl
def hatch_register_build_hook() -> type[RollupBuildHook]:
    return RollupBuildHook
