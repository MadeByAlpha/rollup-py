"""Build the fixture uv workspace for real: `uv lock`, `rollup-py build`, `uv build`, `uv sync`, hatch.

These tests need network access to PyPI (through uv) and take a while; select them with `-m e2e`
or skip them with `-m "not e2e"`.
"""

import email.parser
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from rollup_py.uvcli import find_uv

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "ws"
VENDORED_TOP_LEVEL = {
    "lib",
    "requests",
    "urllib3",
    "idna",
    "charset_normalizer",
    "markupsafe",
    "six.py",
    "socks.py",
    "sockshandler.py",
}


def run(
    *args: str | Path, cwd: Path, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    if check and result.returncode != 0:
        pytest.fail(f"{args} failed with {result.returncode}:\n{result.stdout}\n{result.stderr}")
    return result


def copy_workspace(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination, ignore=shutil.ignore_patterns("uv.lock", "dist", ".venv"))
    run(find_uv(), "lock", cwd=destination)
    return destination


def rollup(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(
        sys.executable, "-m", "rollup_py", "build", "--package", "app", *args, cwd=workspace, check=check
    )


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_workspace(tmp_path_factory.mktemp("e2e") / "ws")


@pytest.fixture(scope="module")
def bundle(workspace: Path) -> Path:
    rollup(workspace, "-o", str(workspace / "bundle"))
    [wheel] = (workspace / "bundle").glob("*.whl")
    return wheel


@pytest.fixture
def wheel_zip(bundle: Path) -> Iterator[zipfile.ZipFile]:
    with zipfile.ZipFile(bundle) as archive:
        yield archive


def test_bundle_name_and_tag(bundle: Path) -> None:
    name, version, python, abi, platform = bundle.stem.split("-")
    assert (name, version) == ("app_bundled", "0.1.0")
    # markupsafe and charset-normalizer ship compiled extensions.
    assert python.startswith("cp") and abi.startswith("cp") and platform != "any"


def test_bundle_layout(wheel_zip: zipfile.ZipFile) -> None:
    top_level = {name.split("/")[0] for name in wheel_zip.namelist()}
    assert top_level == {"app", "app_bundled-0.1.0.dist-info", *VENDORED_TOP_LEVEL}
    # External packages and vendored scripts stay out.
    assert not any(name.startswith(("certifi/", "bin/")) for name in wheel_zip.namelist())


def test_bundle_metadata(wheel_zip: zipfile.ZipFile) -> None:
    dist_info = "app_bundled-0.1.0.dist-info"
    metadata = email.parser.HeaderParser().parsestr(wheel_zip.read(f"{dist_info}/METADATA").decode())
    assert metadata["Name"] == "app-bundled"
    requires = metadata.get_all("Requires-Dist", [])
    assert any(req.startswith("certifi") for req in requires)
    assert 'colorama; sys_platform == "win32"' in requires
    assert "orjson; extra == 'fast'" in requires
    assert not any(req.split(";")[0].startswith(("lib", "requests", "six", "markupsafe")) for req in requires)

    manifest = json.loads(wheel_zip.read(f"{dist_info}/rollup.json"))
    assert {entry["name"].lower() for entry in manifest["vendored"]} >= {
        "lib",
        "requests",
        "markupsafe",
        "six",
    }

    assert "app = app:main" in wheel_zip.read(f"{dist_info}/entry_points.txt").decode()
    assert any(
        name.startswith(f"{dist_info}/vendor/requests-") and name.endswith("LICENSE")
        for name in wheel_zip.namelist()
    )


def test_bundle_installs_and_runs(bundle: Path, tmp_path: Path) -> None:
    uv = find_uv()
    venv = tmp_path / "venv"
    run(uv, "venv", "--python", sys.executable, venv, cwd=tmp_path)
    env = {"VIRTUAL_ENV": str(venv)}
    run(uv, "pip", "install", bundle, cwd=tmp_path, env=env)

    installed = run(uv, "pip", "freeze", cwd=tmp_path, env=env).stdout
    assert {line.split("==")[0].split(" @ ")[0] for line in installed.splitlines()} == {
        "app-bundled",
        "certifi",
    }

    output = run(venv / "bin" / "app", cwd=tmp_path).stdout
    assert "'lib': 'HELLO'" in output
    assert "'markupsafe': '&lt;b&gt;'" in output


def test_standard_build_is_untouched(workspace: Path) -> None:
    run(find_uv(), "build", "--package", "app", "-o", workspace / "std", cwd=workspace)
    [wheel] = (workspace / "std").glob("*.whl")
    [sdist] = (workspace / "std").glob("*.tar.gz")
    assert wheel.name == "app-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        assert {name.split("/")[0] for name in archive.namelist()} == {"app", "app-0.1.0.dist-info"}
        metadata = archive.read("app-0.1.0.dist-info/METADATA").decode()
    assert "Requires-Dist: lib" in metadata
    assert "Requires-Dist: requests[socks]>=2.31" in metadata

    with tarfile.open(sdist) as archive:
        names = {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}
    assert names == {"src/app/__init__.py", "pyproject.toml", "PKG-INFO"}


def test_editable_sync_uses_real_dependencies(workspace: Path) -> None:
    uv = find_uv()
    run(uv, "sync", "--package", "app", cwd=workspace)
    output = run(
        uv, "run", "--package", "app", "python", "-c", "import app; print(app.describe())", cwd=workspace
    ).stdout
    assert "'lib': 'HELLO'" in output


def test_stale_lock_is_rejected(tmp_path: Path) -> None:
    workspace = copy_workspace(tmp_path / "ws")
    pyproject = workspace / "packages" / "app" / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace('"markupsafe>=2",', '"markupsafe>=2",\n    "idna>=3",')
    )
    result = rollup(workspace, check=False)
    assert result.returncode == 1
    assert "error: `uv lock --check` failed" in result.stderr


def test_hatch_build_target(tmp_path: Path) -> None:
    workspace = copy_workspace(tmp_path / "ws")
    app = workspace / "packages" / "app"
    pyproject = app / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            'requires = ["hatchling"]', f'requires = ["hatchling", "rollup-py @ {REPO.as_uri()}"]'
        )
    )
    env = {
        "HATCH_PYTHON": sys.executable,
        "HATCH_DATA_DIR": str(tmp_path / "hatch-data"),
        "HATCH_CACHE_DIR": str(tmp_path / "hatch-cache"),
    }
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    run(find_uv(), "tool", "run", "--python", python, "hatch", "build", "-t", "rollup", cwd=app, env=env)
    assert [wheel.name.split("-")[0] for wheel in (app / "dist").glob("*.whl")] == ["app_bundled"]
