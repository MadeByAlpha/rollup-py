"""`[tool.hatch.build.targets.rollup]` options."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from packaging.utils import NormalizedName, canonicalize_name

from rollup_py.errors import RollupError

TARGET_NAME = "rollup"
TABLE = f"tool.hatch.build.targets.{TARGET_NAME}"

# The CLI forwards its target-environment flags through these, so `hatch build -t rollup` can use them too.
ENV_PYTHON_VERSION = "ROLLUP_PY_PYTHON_VERSION"
ENV_PYTHON_PLATFORM = "ROLLUP_PY_PYTHON_PLATFORM"

type Conditional = Literal["external", "evaluate"]


@dataclass(frozen=True, slots=True)
class RollupConfig:
    distribution_name: str
    # `None` means every direct dependency (`vendor = ["*"]`).
    vendor: frozenset[NormalizedName] | None
    external: frozenset[NormalizedName]
    transitive: bool
    conditional: Conditional
    # `None` means "search upwards from the project root".
    lock: str | None
    check_lock: bool
    python_version: str | None
    python_platform: str | None

    @classmethod
    def from_target_config(
        cls, config: Mapping[str, Any], project_name: str, environ: Mapping[str, str] = os.environ
    ) -> RollupConfig:
        distribution_name = _get_str(config, "distribution-name") or f"{project_name}-rollup"
        if canonicalize_name(distribution_name) == canonicalize_name(project_name):
            raise RollupError(
                f"`{TABLE}.distribution-name` must differ from the project name `{project_name}`, "
                "otherwise the bundled wheel collides with the standard wheel"
            )

        raw_vendor = _get_str_list(config, "vendor", default=["*"])
        if "*" in raw_vendor:
            if len(raw_vendor) != 1:
                raise RollupError(f'`{TABLE}.vendor` cannot mix "*" with package names')
            vendor = None
        else:
            vendor = frozenset(canonicalize_name(name) for name in raw_vendor)

        external = frozenset(
            canonicalize_name(name) for name in _get_str_list(config, "external", default=[])
        )
        if vendor is not None and (overlap := vendor & external):
            raise RollupError(
                f"`{TABLE}`: packages listed in both `vendor` and `external`: {', '.join(sorted(overlap))}"
            )

        conditional = _get_str(config, "conditional") or "external"
        if conditional not in ("external", "evaluate"):
            raise RollupError(f'`{TABLE}.conditional` must be "external" or "evaluate", not {conditional!r}')

        lock = _get_str(config, "lock") or "auto"

        return cls(
            distribution_name=distribution_name,
            vendor=vendor,
            external=external,
            transitive=_get_bool(config, "transitive", default=True),
            conditional=conditional,
            lock=None if lock == "auto" else lock,
            check_lock=_get_bool(config, "check-lock", default=True),
            python_version=environ.get(ENV_PYTHON_VERSION) or _get_str(config, "python-version"),
            python_platform=environ.get(ENV_PYTHON_PLATFORM) or _get_str(config, "python-platform"),
        )


def _get_str(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RollupError(f"`{TABLE}.{key}` must be a string")
    return value


def _get_bool(config: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise RollupError(f"`{TABLE}.{key}` must be a boolean")
    return value


def _get_str_list(config: Mapping[str, Any], key: str, *, default: list[str]) -> list[str]:
    value = config.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in cast("list[Any]", value)):
        raise RollupError(f"`{TABLE}.{key}` must be an array of strings")
    return cast("list[str]", value)
