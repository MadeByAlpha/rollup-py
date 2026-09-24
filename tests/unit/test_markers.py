import pytest
from packaging.markers import Marker

from rollup_py.markers import split_extras, target_environment


@pytest.mark.parametrize(
    ("marker", "expected_marker", "expected_extras"),
    [
        (None, None, set[str]()),
        ('extra == "socks"', None, {"socks"}),
        ('python_version < "3.11" and extra == "Fast_Path"', 'python_version < "3.11"', {"fast-path"}),
        ('(extra == "a" or extra == "b") and sys_platform == "win32"', 'sys_platform == "win32"', {"a", "b"}),
        (
            'sys_platform == "linux" and (python_version < "3.13" or platform_machine == "x86_64")',
            'sys_platform == "linux" and (python_version < "3.13" or platform_machine == "x86_64")',
            set[str](),
        ),
    ],
)
def test_split_extras(marker: str | None, expected_marker: str | None, expected_extras: set[str]) -> None:
    stripped, extras = split_extras(Marker(marker) if marker else None)
    assert (str(stripped) if stripped else None) == expected_marker
    assert extras == expected_extras


def test_target_environment_overrides() -> None:
    env = target_environment("3.13", "aarch64-apple-darwin")
    assert env["python_version"] == "3.13"
    assert env["python_full_version"] == "3.13.0"
    assert env["sys_platform"] == "darwin"
    assert env["platform_machine"] == "arm64"


def test_target_environment_bare_platform_keeps_machine() -> None:
    current = target_environment(None, None)["platform_machine"]
    env = target_environment(None, "windows")
    assert env["sys_platform"] == "win32"
    assert env["platform_machine"] == current
