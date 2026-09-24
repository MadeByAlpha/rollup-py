from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from rollup_py.lock import Dependency, Hoisted, LockedPackage
from rollup_py.metadata import hoisted_requirements, rewrite_core_metadata

METADATA = """\
Metadata-Version: 2.4
Name: app
Version: 0.1.0
Requires-Dist: lib
Requires-Dist: requests[socks]>=2.31
Requires-Dist: certifi>=2023
Provides-Extra: compat
Requires-Dist: six; extra == 'compat'
Requires-Dist: certifi>=2023

# App

Requires-Dist: this is description text, not a header
"""


def test_rewrite_core_metadata() -> None:
    result = rewrite_core_metadata(METADATA, name="app-bundled", drop={"lib", "requests", "six"})
    header, _, body = result.partition("\n\n")
    assert header.splitlines() == [
        "Metadata-Version: 2.4",
        "Name: app-bundled",
        "Version: 0.1.0",
        "Requires-Dist: certifi>=2023",
        "Provides-Extra: compat",
    ]
    assert body == "# App\n\nRequires-Dist: this is description text, not a header\n"


def test_rewrite_core_metadata_without_body() -> None:
    assert rewrite_core_metadata("Name: a\nVersion: 1\n", name="b", drop=()) == "Name: b\nVersion: 1\n"


def package(name: str, requires: list[str] | None) -> LockedPackage:
    return LockedPackage(
        name=canonicalize_name(name),
        version="1",
        source={"registry": "https://pypi.org/simple"},
        dependencies=[],
        optional_dependencies={},
        sdist=None,
        wheels=[],
        requires_dist=None if requires is None else [Requirement(r) for r in requires],
    )


def test_hoisted_requirements_keep_specifier_and_environment_marker(tmp_path: Path) -> None:
    requests = package(
        "requests",
        [
            "certifi>=2017.4.17",
            "idna<4,>=2.5",
            'PySocks!=1.5.7,>=1.5.6; extra == "socks"',
            'chardet<6,>=3.0.2; extra == "use-chardet-on-py3"',
            'win-inet-pton; sys_platform == "win32" and extra == "socks"',
        ],
    )
    items = [
        Hoisted(requests, Dependency(name=canonicalize_name("certifi")), frozenset({"socks"})),
        Hoisted(requests, Dependency(name=canonicalize_name("pysocks")), frozenset({"socks"})),
        Hoisted(requests, Dependency(name=canonicalize_name("chardet")), frozenset({"socks"})),
        Hoisted(
            requests,
            Dependency(name=canonicalize_name("win-inet-pton"), marker="sys_platform == 'win32'"),
            frozenset({"socks"}),
        ),
    ]
    assert hoisted_requirements(items, {}) == [
        "certifi>=2017.4.17",
        "PySocks!=1.5.7,>=1.5.6",
        # chardet belongs to an extra that was not requested: falls back to the lock's (bare) edge.
        "chardet",
        'win-inet-pton; sys_platform == "win32"',
    ]


def test_hoisted_requirements_are_deduplicated() -> None:
    lib = package("lib", ["six>=1.16"])
    other = package("other", ["six>=1.16"])
    items = [
        Hoisted(lib, Dependency(name=canonicalize_name("six")), frozenset()),
        Hoisted(other, Dependency(name=canonicalize_name("six")), frozenset()),
    ]
    assert hoisted_requirements(items, {}) == ["six>=1.16"]
