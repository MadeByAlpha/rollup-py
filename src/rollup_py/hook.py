"""The `rollup` build hook: stages vendored files and feeds them to hatchling's wheel writer."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

from rollup_py.bundle import Log, build_bundle
from rollup_py.config import TARGET_NAME, RollupConfig
from rollup_py.errors import RollupError

BUILD_DATA_KEY = "rollup_py"


class RollupBuildHook(BuildHookInterface[Any]):
    """Attached automatically by the `rollup` target; it is not meant to be configured by hand."""

    PLUGIN_NAME = TARGET_NAME

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stage: tempfile.TemporaryDirectory[str] | None = None

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != TARGET_NAME:
            raise RollupError(
                "The `rollup` hook only runs as part of the `rollup` build target "
                f"(not `{self.target_name}`); move its options to `[tool.hatch.build.targets.rollup]` "
                "and build with `rollup-py build`"
            )

        config = RollupConfig.from_target_config(self.config, self.metadata.core.name)
        project_files = {
            included.distribution_path.replace("\\", "/"): included.relative_path or included.path
            for included in self.build_config.builder.recurse_included_files()
        }
        for source, target in build_data["force_include"].items():
            project_files[str(target)] = str(source)

        def reserved(path: str) -> str | None:
            return f"{self.metadata.core.name} ({project_files[path]})" if path in project_files else None

        self._stage = tempfile.TemporaryDirectory(prefix="rollup-py-")
        bundle = build_bundle(
            project_root=Path(self.root),
            project_name=self.metadata.core.name,
            config=config,
            stage_dir=Path(self._stage.name),
            reserved=reserved,
            log=Log(info=self.app.display_info, warning=self.app.display_warning),
        )

        build_data["force_include"].update({str(source): path for path, source in bundle.files.items()})
        build_data["dependencies"].extend(bundle.hoisted)
        if bundle.tag is not None:
            if "tag" in build_data or build_data.get("infer_tag"):
                self.app.display_warning(
                    "rollup: another build hook already chose the wheel tag; "
                    f"the vendored extensions would need `{bundle.tag}`"
                )
            else:
                build_data["tag"] = bundle.tag
            build_data["pure_python"] = False
        build_data[BUILD_DATA_KEY] = bundle

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if self._stage is not None:
            self._stage.cleanup()
            self._stage = None
