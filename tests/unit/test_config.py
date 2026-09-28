import pytest

from rollup_py.config import ENV_PYTHON_VERSION, RollupConfig
from rollup_py.errors import RollupError


def test_defaults() -> None:
    config = RollupConfig.from_target_config({}, "app", environ={})
    assert config.distribution_name == "app-rollup"
    assert config.vendor is None
    assert config.external == frozenset()
    assert config.extras == frozenset()
    assert config.groups == frozenset()
    assert config.transitive
    assert config.conditional == "external"
    assert config.lock is None
    assert config.check_lock


def test_names_are_normalized() -> None:
    config = RollupConfig.from_target_config(
        {
            "vendor": ["Requests", "zope.interface"],
            "external": ["Typing_Extensions"],
            "extras": ["Prod_Patched"],
            "groups": ["Dev.Original"],
        },
        "app",
        environ={},
    )
    assert config.vendor == {"requests", "zope-interface"}
    assert config.external == {"typing-extensions"}
    assert config.extras == {"prod-patched"}
    assert config.groups == {"dev-original"}


def test_environment_overrides_config() -> None:
    config = RollupConfig.from_target_config(
        {"python-version": "3.12"}, "app", environ={ENV_PYTHON_VERSION: "3.13"}
    )
    assert config.python_version == "3.13"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"distribution-name": "App"}, "must differ from the project name"),
        ({"vendor": ["*", "requests"]}, "cannot mix"),
        ({"vendor": ["requests"], "external": ["requests"]}, "both `vendor` and `external`"),
        ({"conditional": "always"}, "must be"),
        ({"transitive": "yes"}, "must be a boolean"),
        ({"external": "certifi"}, "must be an array of strings"),
        ({"groups": "prod"}, "must be an array of strings"),
    ],
)
def test_invalid(options: dict[str, object], message: str) -> None:
    with pytest.raises(RollupError, match=message):
        RollupConfig.from_target_config(options, "app", environ={})
