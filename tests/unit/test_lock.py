from pathlib import Path

import pytest
from packaging.utils import canonicalize_name

from rollup_py.config import RollupConfig
from rollup_py.errors import RollupError
from rollup_py.lock import Dependency, Hoisted, Lockfile, VendorPlan, find_lockfile, plan_vendoring
from rollup_py.markers import target_environment

LINUX = target_environment("3.12", "x86_64-unknown-linux-gnu")
WINDOWS = target_environment("3.12", "x86_64-pc-windows-msvc")

LOCK = """\
version = 1
revision = 3
requires-python = ">=3.12"

[[package]]
name = "app"
version = "0.1.0"
source = { editable = "packages/app" }
dependencies = [
    { name = "lib" },
    { name = "markupsafe" },
    { name = "requests", extra = ["socks"] },
]

[package.optional-dependencies]
fast = [{ name = "orjson" }]

[package.metadata]
requires-dist = [
    { name = "lib", editable = "packages/lib" },
    { name = "markupsafe", specifier = ">=2" },
    { name = "orjson", marker = "extra == 'fast'" },
    { name = "requests", extras = ["socks"], specifier = ">=2.31" },
]

[[package]]
name = "lib"
version = "0.1.0"
source = { editable = "packages/lib" }
dependencies = [
    { name = "colorama", marker = "sys_platform == 'win32'" },
    { name = "six" },
]

[package.metadata]
requires-dist = [
    { name = "colorama", marker = "sys_platform == 'win32'" },
    { name = "six", specifier = ">=1.16" },
]

[[package]]
name = "requests"
version = "2.32.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "certifi" }, { name = "idna" }]
sdist = { url = "https://example.invalid/requests.tar.gz", hash = "sha256:aa" }
wheels = [{ url = "https://example.invalid/requests.whl", hash = "sha256:bb" }]

[package.optional-dependencies]
socks = [{ name = "pysocks" }]

[[package]]
name = "certifi"
version = "2024.1.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "colorama"
version = "0.4.6"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "idna"
version = "3.7"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "markupsafe"
version = "3.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "orjson"
version = "3.10.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pysocks"
version = "1.7.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "six"
version = "1.16.0"
source = { registry = "https://pypi.org/simple" }
"""


@pytest.fixture
def lock(tmp_path: Path) -> Lockfile:
    path = tmp_path / "uv.lock"
    path.write_text(LOCK)
    return Lockfile.load(path)


def config(**options: object) -> RollupConfig:
    return RollupConfig.from_target_config(options, "app", environ={})


def hoisted(items: list[Hoisted]) -> set[tuple[str, str]]:
    return {(item.parent.name, item.dependency.name) for item in items}


def test_vendors_everything_by_default(lock: Lockfile) -> None:
    plan = plan_vendoring(lock, "app", config(external=["certifi"]), LINUX)
    assert plan.vendored_names == {"lib", "markupsafe", "requests", "six", "idna", "pysocks"}
    assert hoisted(plan.hoisted) == {("requests", "certifi"), ("lib", "colorama")}
    # The extra requested on the edge is carried with the vendored package.
    requests = next(extras for package, extras in plan.vendored.values() if package.name == "requests")
    assert requests == {"socks"}


def test_without_transitive_only_roots_are_vendored(lock: Lockfile) -> None:
    plan = plan_vendoring(lock, "app", config(transitive=False), LINUX)
    assert plan.vendored_names == {"lib", "markupsafe", "requests"}
    assert hoisted(plan.hoisted) == {
        ("lib", "colorama"),
        ("lib", "six"),
        ("requests", "certifi"),
        ("requests", "idna"),
        ("requests", "pysocks"),
    }


def test_whitelist(lock: Lockfile) -> None:
    plan = plan_vendoring(lock, "app", config(vendor=["lib"]), LINUX)
    assert plan.vendored_names == {"lib", "six"}


def test_whitelist_must_name_direct_dependencies(lock: Lockfile) -> None:
    with pytest.raises(RollupError, match="not direct dependencies"):
        plan_vendoring(lock, "app", config(vendor=["six"]), LINUX)


def test_conditional_evaluate(lock: Lockfile) -> None:
    on_windows = plan_vendoring(lock, "app", config(conditional="evaluate"), WINDOWS)
    assert "colorama" in on_windows.vendored_names

    on_linux = plan_vendoring(lock, "app", config(conditional="evaluate"), LINUX)
    assert "colorama" not in on_linux.vendored_names
    # A marker that is false for the target is dropped rather than hoisted.
    assert ("lib", "colorama") not in hoisted(on_linux.hoisted)


def test_external_package_depending_on_vendored_one_is_rejected(lock: Lockfile) -> None:
    # `six` is vendored through `lib`, but an external `requests` pulling `six` would clash.
    text = LOCK.replace(
        'dependencies = [{ name = "certifi" }, { name = "idna" }]',
        'dependencies = [{ name = "certifi" }, { name = "idna" }, { name = "six" }]',
    )
    lock.path.write_text(text)
    with pytest.raises(RollupError, match=r"`six` is vendored but is also required.*app -> requests -> six"):
        plan_vendoring(Lockfile.load(lock.path), "app", config(external=["requests"]), LINUX)


def test_forked_versions_resolve_by_version(tmp_path: Path) -> None:
    path = tmp_path / "uv.lock"
    path.write_text(
        LOCK
        + """
[[package]]
name = "six"
version = "1.17.0"
source = { registry = "https://pypi.org/simple" }
"""
    )
    lock = Lockfile.load(path)
    assert lock.resolve(Dependency(name=canonicalize_name("six"), version="1.17.0")).version == "1.17.0"
    with pytest.raises(RollupError, match="cannot resolve `six`"):
        lock.resolve(Dependency(name=canonicalize_name("six")))


def test_hashes_cover_sdist_and_wheels(lock: Lockfile) -> None:
    requests = lock.resolve(Dependency(name=canonicalize_name("requests")))
    assert requests.hashes == ["sha256:aa", "sha256:bb"]


def test_find_lockfile_walks_up(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(LOCK)
    nested = tmp_path / "packages" / "app"
    nested.mkdir(parents=True)
    assert find_lockfile(nested, None) == tmp_path / "uv.lock"
    with pytest.raises(RollupError, match="does not exist"):
        find_lockfile(nested, "missing.lock")


# A patched fork of `six` in the `prod` extra and the original in the `dev` group, as uv locks them.
CONFLICT_LOCK = """\
version = 1
revision = 3
requires-python = ">=3.12"
conflicts = [[
    { package = "app", extra = "prod" },
    { package = "app", group = "dev" },
]]

[[package]]
name = "app"
version = "0.1.0"
source = { editable = "." }
dependencies = [{ name = "python-dateutil" }]

[package.optional-dependencies]
prod = [
    { name = "six", version = "1.16.0", source = { git = "https://github.com/benjaminp/six?tag=1.16.0#65486e4" } },
]

[package.dev-dependencies]
dev = [
    { name = "six", version = "1.17.0", source = { registry = "https://pypi.org/simple" } },
]

[package.metadata]
requires-dist = [
    { name = "python-dateutil" },
    { name = "six", marker = "extra == 'prod'", git = "https://github.com/benjaminp/six?tag=1.16.0" },
]
provides-extras = ["prod", "docs"]

[package.metadata.requires-dev]
dev = [{ name = "six", specifier = "==1.17.0" }]

[[package]]
name = "python-dateutil"
version = "2.9.0.post0"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "six", version = "1.16.0", source = { git = "https://github.com/benjaminp/six?tag=1.16.0#65486e4" }, marker = "extra == 'extra-3-app-prod'" },
    { name = "six", version = "1.17.0", source = { registry = "https://pypi.org/simple" }, marker = "extra == 'group-3-app-dev' or extra != 'extra-3-app-prod'" },
]

[[package]]
name = "six"
version = "1.16.0"
source = { git = "https://github.com/benjaminp/six?tag=1.16.0#65486e4" }

[[package]]
name = "six"
version = "1.17.0"
source = { registry = "https://pypi.org/simple" }
"""  # noqa: E501


@pytest.fixture
def conflict_lock(tmp_path: Path) -> Lockfile:
    path = tmp_path / "uv.lock"
    path.write_text(CONFLICT_LOCK)
    return Lockfile.load(path)


def vendored_versions(plan: VendorPlan) -> dict[str, str | None]:
    return {package.name: package.version for package in plan.packages}


@pytest.mark.parametrize(
    ("options", "six"),
    [
        ({}, "1.17.0"),
        ({"extras": ["prod"]}, "1.16.0"),
        ({"groups": ["dev"]}, "1.17.0"),
    ],
)
def test_selected_extra_or_group_picks_its_fork(
    conflict_lock: Lockfile, options: dict[str, object], six: str
) -> None:
    plan = plan_vendoring(conflict_lock, "app", config(**options), LINUX)
    assert vendored_versions(plan) == {"python-dateutil": "2.9.0.post0", "six": six}
    assert plan.hoisted == []


def test_selected_extra_that_is_external_becomes_a_requirement(conflict_lock: Lockfile) -> None:
    plan = plan_vendoring(conflict_lock, "app", config(extras=["prod"], external=["six"]), LINUX)
    assert vendored_versions(plan) == {"python-dateutil": "2.9.0.post0"}
    project_items = [item for item in plan.hoisted if item.parent.name == "app"]
    assert [(item.dependency.version, item.parent_extras) for item in project_items] == [("1.16.0", {"prod"})]
    # Only the edge of the selected fork is left; its conflict marker is settled.
    assert [(item.parent.name, item.dependency.version, item.dependency.marker) for item in plan.hoisted] == [
        ("app", "1.16.0", None),
        ("python-dateutil", "1.16.0", None),
    ]


def test_selected_group_outside_the_whitelist_becomes_a_requirement(conflict_lock: Lockfile) -> None:
    plan = plan_vendoring(conflict_lock, "app", config(vendor=["python-dateutil"], groups=["dev"]), LINUX)
    assert vendored_versions(plan) == {"python-dateutil": "2.9.0.post0", "six": "1.17.0"}
    assert [(item.parent.name, item.parent_groups) for item in plan.hoisted] == [("app", {"dev"})]


def test_whitelist_accepts_dependencies_of_selected_extras(conflict_lock: Lockfile) -> None:
    # `six` is accepted as a direct dependency, but the external `python-dateutil` still needs it.
    with pytest.raises(RollupError, match=r"`six` is vendored.*app -> python-dateutil -> six"):
        plan_vendoring(conflict_lock, "app", config(vendor=["six"], extras=["prod"]), LINUX)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"extras": ["nope"]}, "`app` does not define: nope"),
        ({"groups": ["prod"]}, "`app` does not define: prod"),
        ({"extras": ["prod"], "groups": ["dev"]}, "extra `prod` and group `dev` conflict"),
    ],
)
def test_invalid_selection(conflict_lock: Lockfile, options: dict[str, object], message: str) -> None:
    with pytest.raises(RollupError, match=message):
        plan_vendoring(conflict_lock, "app", config(**options), LINUX)


def test_extra_without_dependencies_can_be_selected(conflict_lock: Lockfile) -> None:
    # `docs` is only listed in `provides-extras`.
    plan = plan_vendoring(conflict_lock, "app", config(extras=["docs"]), LINUX)
    assert vendored_versions(plan)["six"] == "1.17.0"
