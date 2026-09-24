from pathlib import Path

import pytest

from rollup_py import fetch as fetch_module
from rollup_py.errors import RollupError
from rollup_py.fetch import _git_url, fetch
from rollup_py.lock import Lockfile

LOCK = """\
version = 1
revision = 3

[[package]]
name = "app"
version = "0.1.0"
source = { editable = "packages/app" }

[[package]]
name = "lib"
version = "0.1.0"
source = { editable = "packages/lib" }

[[package]]
name = "meta"
version = "0.1.0"
source = { virtual = "packages/meta" }

[[package]]
name = "six"
version = "1.17.0"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://example.invalid/six.whl", hash = "sha256:11" }]

[[package]]
name = "private"
version = "2.0"
source = { registry = "https://pypi.example.com/simple" }
sdist = { url = "https://example.invalid/private.tar.gz", hash = "sha256:22" }

[[package]]
name = "click"
version = "8.2.0"
source = { git = "https://github.com/pallets/click?rev=main#0123abcd" }
"""


def test_git_url() -> None:
    assert _git_url("https://github.com/pallets/click?rev=main#0123abcd") == (
        "git+https://github.com/pallets/click@0123abcd"
    )
    assert _git_url("https://github.com/org/mono?subdirectory=pkgs%2Fcore&tag=v1#beef") == (
        "git+https://github.com/org/mono@beef#subdirectory=pkgs/core"
    )
    with pytest.raises(RollupError, match="no pinned commit"):
        _git_url("https://github.com/org/repo?rev=main")


def test_fetch_groups_by_index_and_pins_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "uv.lock").write_text(LOCK)
    lock = Lockfile.load(tmp_path / "uv.lock")
    calls: list[tuple[list[str], list[str]]] = []

    def fake_run_uv(args: list[str], *, cwd: Path) -> str:
        assert cwd == tmp_path
        requirements = Path(args[args.index("-r") + 1]).read_text().splitlines()
        calls.append((args, requirements))
        return ""

    monkeypatch.setattr(fetch_module, "run_uv", fake_run_uv)
    target = tmp_path / "stage" / "site"
    target.mkdir(parents=True)
    packages = [p for p in lock.packages if p.name != "app"]
    fetch(lock, packages, target, python_version="3.12", python_platform=None)

    assert [requirements for _, requirements in calls] == [
        # Grouped per index, in a stable (sorted) order.
        ["private==2.0 --hash=sha256:22"],
        ["six==1.17.0 --hash=sha256:11"],
        [
            f"lib @ {(tmp_path / 'packages' / 'lib').as_uri()}",
            "click @ git+https://github.com/pallets/click@0123abcd",
        ],
    ]
    first, second, local = (args for args, _ in calls)
    assert first[first.index("--index-url") + 1] == "https://pypi.example.com/simple"
    assert second[second.index("--index-url") + 1] == "https://pypi.org/simple"
    assert "--require-hashes" in first and "--require-hashes" not in local
    assert first[first.index("--python-version") + 1] == "3.12"
    assert {"--target", "--no-deps"} <= set(local)
