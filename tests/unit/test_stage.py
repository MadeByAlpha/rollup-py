import re
from pathlib import Path

import pytest
from packaging.utils import canonicalize_name

from rollup_py.errors import RollupError
from rollup_py.lock import LockedPackage
from rollup_py.stage import collect, merge


def install(target: Path, name: str, files: dict[str, str], *, tag: str = "py3-none-any") -> None:
    """Lay out a distribution the way `uv pip install --target` does."""
    dist_info = target / f"{name}-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    (dist_info / "WHEEL").write_text(f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n")
    (dist_info / "LICENSE").write_text("MIT")
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    record = [*files, f"{dist_info.name}/METADATA", f"{dist_info.name}/RECORD,,"]
    (dist_info / "RECORD").write_text("\n".join(f"{line},sha256=x,1" for line in record) + "\n")


def locked(name: str) -> LockedPackage:
    return LockedPackage(
        name=canonicalize_name(name),
        version="1.0",
        source={"registry": "https://pypi.org/simple"},
        dependencies=[],
        optional_dependencies={},
        sdist=None,
        wheels=[],
        requires_dist=None,
    )


def test_collect_keeps_importables_and_drops_scheme_files(tmp_path: Path) -> None:
    install(
        tmp_path,
        "tool",
        {
            "tool/__init__.py": "",
            "tool/_speedups.cpython-312-x86_64-linux-gnu.so": "",
            "tool/__pycache__/__init__.cpython-312.pyc": "",
            "tool_helper.py": "",
            "tool.pth": "",
            "bin/tool": "#!python",
            "share/man/tool.1": "",
            "README.md": "",
        },
        tag="cp312-cp312-manylinux_2_17_x86_64",
    )
    [dist] = collect(tmp_path, [locked("tool")])
    assert sorted(dist.files) == [
        "tool.pth",
        "tool/__init__.py",
        "tool/_speedups.cpython-312-x86_64-linux-gnu.so",
        "tool_helper.py",
    ]
    assert sorted(dist.dropped) == ["README.md", "bin/tool", "share/man/tool.1"]
    assert dist.top_level == ["tool", "tool.pth", "tool_helper.py"]
    assert not dist.is_pure
    assert set(dist.license_files()) == {"METADATA", "LICENSE"}


def test_package_named_like_a_scheme_directory_is_kept(tmp_path: Path) -> None:
    install(tmp_path, "etc", {"etc/__init__.py": ""})
    [dist] = collect(tmp_path, [locked("etc")])
    assert list(dist.files) == ["etc/__init__.py"]


def test_collect_reports_missing_and_unexpected(tmp_path: Path) -> None:
    install(tmp_path, "one", {"one.py": ""})
    with pytest.raises(RollupError, match="did not install: two"):
        collect(tmp_path, [locked("one"), locked("two")])
    with pytest.raises(RollupError, match="Unexpected distribution `one`"):
        collect(tmp_path, [])


def test_merge_allows_namespace_directories_but_not_file_clashes(tmp_path: Path) -> None:
    install(tmp_path, "ns-a", {"ns/a/__init__.py": ""})
    install(tmp_path, "ns-b", {"ns/b/__init__.py": ""})
    dists = collect(tmp_path, [locked("ns-a"), locked("ns-b")])
    assert list(merge(dists, lambda _: None)) == ["ns/a/__init__.py", "ns/b/__init__.py"]

    with pytest.raises(
        RollupError, match=re.escape("`ns/a/__init__.py` is provided by both `app` and vendored `ns-a`")
    ):
        merge(dists, lambda path: "app" if path == "ns/a/__init__.py" else None)


def test_merge_rejects_two_vendored_owners(tmp_path: Path) -> None:
    install(tmp_path, "first", {"shared.py": ""})
    install(tmp_path, "second", {"shared.py": ""})
    dists = collect(tmp_path, [locked("first"), locked("second")])
    with pytest.raises(
        RollupError, match=re.escape("`shared.py` is provided by both `first` and vendored `second`")
    ):
        merge(dists, lambda _: None)
