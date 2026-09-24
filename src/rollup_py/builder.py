"""The `rollup` build target: a wheel builder that bundles dependencies under a different name."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import cached_property
from pathlib import Path
from typing import Any, cast

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.builders.plugin.interface import IncludedFile
from hatchling.builders.wheel import RecordFile, WheelArchive, WheelBuilder
from packaging.utils import canonicalize_name

from rollup_py.bundle import MANIFEST_NAME, Bundle
from rollup_py.config import TARGET_NAME, RollupConfig
from rollup_py.errors import RollupError
from rollup_py.hook import BUILD_DATA_KEY, RollupBuildHook
from rollup_py.metadata import rewrite_core_metadata

# Wheel-target options that make no sense for this target (it only has a `standard` version).
_NOT_INHERITED = frozenset({"versions", "dev-mode-dirs", "dev-mode-exact"})


class RollupBuilder(WheelBuilder):
    """`hatch build -t rollup` / `rollup-py build`.

    File selection is inherited from `[tool.hatch.build.targets.wheel]` and can be overridden in
    `[tool.hatch.build.targets.rollup]`, so the bundle ships exactly what the standard wheel ships
    plus the vendored packages.
    """

    PLUGIN_NAME = TARGET_NAME

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._merged_target_config: dict[str, Any] | None = None
        self._bundle: Bundle | None = None

    @property
    def target_config(self) -> dict[str, Any]:
        if self._merged_target_config is None:
            targets = self.metadata.hatch.build_targets
            wheel = _table(targets, "wheel")
            rollup = _table(targets, TARGET_NAME)
            merged = {key: value for key, value in wheel.items() if key not in _NOT_INHERITED}
            merged.update(rollup)
            merged["hooks"] = {**_table(wheel, "hooks"), **_table(rollup, "hooks")}
            self._merged_target_config = merged
        return self._merged_target_config

    @cached_property
    def rollup_config(self) -> RollupConfig:
        return RollupConfig.from_target_config(self.target_config, self.metadata.core.name)

    def get_version_api(self) -> dict[str, Callable[..., str]]:
        return {"standard": self.build_standard}

    def get_default_versions(self) -> list[str]:
        return ["standard"]

    def get_build_hooks(self, directory: str) -> dict[str, BuildHookInterface[Any]]:
        hooks: dict[str, BuildHookInterface[Any]] = super().get_build_hooks(directory)  # pyright: ignore[reportUnknownVariableType]
        # Always last, so files contributed by the project's own hooks are known when checking for clashes.
        hooks.pop(TARGET_NAME, None)
        hooks[TARGET_NAME] = RollupBuildHook(
            self.root, self.target_config, self.config, self.metadata, directory, self.PLUGIN_NAME, self.app
        )
        return hooks

    def clean(self, directory: str, versions: list[str]) -> None:
        # Unlike the wheel target, leave the standard wheels that usually share `dist/` alone.
        for wheel in Path(directory).glob(f"{self._file_name_component}-*.whl"):
            wheel.unlink()

    @property
    def _file_name_component(self) -> str:
        name = self.rollup_config.distribution_name
        return self.normalize_file_name_component(
            canonicalize_name(name) if self.config.strict_naming else name
        )

    @property
    def artifact_project_id(self) -> str:
        return f"{self._file_name_component}-{self.metadata.version}"

    def build_standard(self, directory: str, **build_data: Any) -> str:
        bundle = build_data.get(BUILD_DATA_KEY)
        if not isinstance(bundle, Bundle):
            raise RollupError("The rollup build hook did not run (are build hooks disabled?)")
        self._bundle = bundle
        try:
            return super().build_standard(directory, **build_data)
        finally:
            self._bundle = None

    def write_project_metadata(
        self, archive: WheelArchive, records: RecordFile, extra_dependencies: Sequence[str] = ()
    ) -> None:
        assert self._bundle is not None
        constructor = cast(Callable[..., str], self.config.core_metadata_constructor)
        text = constructor(self.metadata, extra_dependencies=extra_dependencies)
        text = rewrite_core_metadata(
            text, name=self.rollup_config.distribution_name, drop=self._bundle.vendored_names
        )
        records.write(archive.write_metadata("METADATA", text))

    def add_extra_metadata(
        self, archive: WheelArchive, records: RecordFile, build_data: dict[str, Any]
    ) -> None:
        super().add_extra_metadata(archive, records, build_data)
        assert self._bundle is not None
        records.write(archive.write_metadata(MANIFEST_NAME, self._bundle.manifest()))
        for relative, source in sorted(self._bundle.metadata_files.items()):
            included = IncludedFile(str(source), "", f"{archive.metadata_directory}/{relative}")
            records.write(archive.add_file(included))


def _table(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key, {})
    if not isinstance(value, dict):
        raise RollupError(f"`{key}` must be a table")
    return value  # pyright: ignore[reportUnknownVariableType]
