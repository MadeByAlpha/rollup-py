from pathlib import Path

import pytest
from packaging.utils import canonicalize_name

from rollup_py.config import RollupConfig
from rollup_py.errors import RollupError
from rollup_py.lock import (
    Dependency,
    Hoisted,
    Lockfile,
    _check_external_overlap,
    find_lockfile,
    plan_vendoring,
)
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


def with_edge(lock: Lockfile, edge: str, text: str = LOCK) -> Lockfile:
    """`lock` rewritten so that `markupsafe` has the single dependency `edge`."""
    anchor = 'name = "markupsafe"\nversion = "3.0.0"\nsource = { registry = "https://pypi.org/simple" }\n'
    lock.path.write_text(text.replace(anchor, f"{anchor}dependencies = [{edge}]\n"))
    return Lockfile.load(lock.path)


def test_conditional_edge_to_vendored_package_is_not_hoisted(lock: Lockfile) -> None:
    # `requests` is vendored through `app`; the conditional `markupsafe -> requests` is served by that
    # copy, so it neither becomes a requirement nor makes `idna` look required by an external package.
    lock = with_edge(lock, """{ name = "requests", marker = "sys_platform != 'emscripten'" }""")
    plan = plan_vendoring(lock, "app", config(external=["certifi"]), LINUX)
    assert "requests" in plan.vendored_names
    assert hoisted(plan.hoisted) == {("requests", "certifi"), ("lib", "colorama")}


def test_conditional_edge_to_vendored_package_carries_its_extras(lock: Lockfile) -> None:
    text = LOCK.replace('{ name = "requests", extra = ["socks"] }', '{ name = "requests" }')
    lock = with_edge(
        lock, """{ name = "requests", extra = ["socks"], marker = "sys_platform != 'emscripten'" }""", text
    )
    plan = plan_vendoring(lock, "app", config(external=["certifi"]), LINUX)
    requests = next(extras for package, extras in plan.vendored.values() if package.name == "requests")
    assert requests == {"socks"}
    assert "pysocks" in plan.vendored_names
    assert ("markupsafe", "requests") not in hoisted(plan.hoisted)


def test_conditional_edge_to_other_version_of_vendored_package_clashes(lock: Lockfile) -> None:
    text = LOCK.replace('    { name = "six" },\n]', '    { name = "six", version = "1.16.0" },\n]') + (
        '\n[[package]]\nname = "six"\nversion = "1.17.0"\nsource = { registry = "https://pypi.org/simple" }\n'
    )
    lock = with_edge(
        lock, """{ name = "six", version = "1.17.0", marker = "sys_platform == 'win32'" }""", text
    )
    with pytest.raises(RollupError, match=r"Two versions of `six`"):
        plan_vendoring(lock, "app", config(), LINUX)


def test_overlap_check_skips_vendored_starts(lock: Lockfile) -> None:
    # A hoisted edge naming a vendored package is not an external package, even if one slips through.
    plan = plan_vendoring(lock, "app", config(external=["certifi"]), LINUX)
    requests = next(package for package in plan.packages if package.name == "requests")
    markupsafe = next(package for package in plan.packages if package.name == "markupsafe")
    plan.hoisted.append(Hoisted(markupsafe, Dependency(name=requests.name), frozenset()))
    _check_external_overlap(lock, plan, config(external=["certifi"]), LINUX)


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
