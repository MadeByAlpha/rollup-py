import pytest
from packaging.markers import Marker

from rollup_py.markers import conflict_item, evaluate_extras, split_extras, target_environment


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


PROD = "extra-3-app-prod"
DEV = "group-3-app-dev"


@pytest.mark.parametrize(
    ("marker", "active", "expected"),
    [
        (f"extra == '{PROD}'", {PROD}, True),
        (f"extra == '{PROD}'", set[str](), False),
        (f"extra == '{DEV}' or extra != '{PROD}'", {PROD}, False),
        (f"extra == '{DEV}' or extra != '{PROD}'", set[str](), True),
        (f"extra == '{DEV}' or extra != '{PROD}'", {DEV}, True),
        (f"sys_platform == 'win32' and extra == '{PROD}'", {PROD}, 'sys_platform == "win32"'),
        (f"sys_platform == 'win32' and extra == '{PROD}'", set[str](), False),
        (
            f"python_version < '3.13' and extra == '{PROD}' or sys_platform == 'win32' and extra == '{DEV}'",
            {PROD},
            'python_version < "3.13"',
        ),
        (
            f"(python_version < '3.13' or sys_platform == 'win32') and extra != '{DEV}'",
            {PROD},
            'python_version < "3.13" or sys_platform == "win32"',
        ),
        (
            f"python_version < '3.13' and (sys_platform == 'win32' or extra == '{PROD}')",
            {DEV},
            'python_version < "3.13" and sys_platform == "win32"',
        ),
    ],
)
def test_evaluate_extras(marker: str, active: set[str], expected: bool | str) -> None:
    result = evaluate_extras(Marker(marker), active)
    assert (result if isinstance(result, bool) else str(result)) == expected


def test_conflict_item_is_normalized() -> None:
    assert conflict_item("extra", "My_App", "Prod_X") == "extra-6-my-app-prod-x"
    assert conflict_item("group", "app", "dev") == DEV


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
