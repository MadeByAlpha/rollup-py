import pytest
from packaging.tags import Tag, parse_tag

from rollup_py.errors import RollupError
from rollup_py.tags import bundle_tag


def tags(*values: str) -> list[Tag]:
    return [tag for value in values for tag in parse_tag(value)]


def test_all_pure_keeps_default_tag() -> None:
    assert bundle_tag([tags("py3-none-any"), tags("py2.py3-none-any")]) is None


def test_single_extension_keeps_its_tag_with_legacy_alias() -> None:
    wheel = tags("cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64")
    assert (
        bundle_tag([wheel, tags("py3-none-any")]) == "cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64"
    )


def test_newest_glibc_floor_and_specific_abi_win() -> None:
    result = bundle_tag(
        [
            tags("cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64"),
            tags("cp38-abi3-manylinux_2_28_x86_64"),
        ]
    )
    assert result == "cp312-cp312-manylinux_2_28_x86_64"


def test_abi3_only_takes_highest_minimum() -> None:
    result = bundle_tag([tags("cp38-abi3-win_amd64"), tags("cp310-abi3-win_amd64")])
    assert result == "cp310-abi3-win_amd64"


def test_macos_universal2_and_arm64() -> None:
    result = bundle_tag([tags("cp312-cp312-macosx_10_13_universal2"), tags("cp312-cp312-macosx_11_0_arm64")])
    assert result == "cp312-cp312-macosx_11_0_arm64"


def test_platform_only_wheel() -> None:
    assert bundle_tag([tags("py3-none-musllinux_1_2_aarch64")]) == "py3-none-musllinux_1_2_aarch64"


def test_local_linux_build_accepts_manylinux_of_same_arch() -> None:
    result = bundle_tag([tags("cp312-cp312-linux_x86_64"), tags("cp312-cp312-manylinux_2_17_x86_64")])
    assert result == "cp312-cp312-linux_x86_64"


@pytest.mark.parametrize(
    "wheels",
    [
        [tags("cp312-cp312-manylinux_2_17_x86_64"), tags("cp312-cp312-manylinux_2_17_aarch64")],
        [tags("cp312-cp312-win_amd64"), tags("cp313-cp313-win_amd64")],
        [tags("cp312-cp312-manylinux_2_17_x86_64"), tags("cp312-cp312-win_amd64")],
    ],
)
def test_incompatible(wheels: list[list[Tag]]) -> None:
    with pytest.raises(RollupError, match="no platform in common"):
        bundle_tag(wheels)
